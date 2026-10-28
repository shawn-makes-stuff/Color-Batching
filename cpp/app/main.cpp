// Local app: settings, processing and preview (port of colorstack/app.py).
//   color-batching-app [file.gcode] [--port 8765] [--no-browser]
// Serves viewer/index.html and keeps uploads and results under work/, with the same HTTP API as the Python app.
// ponytail: the page opens in the default browser, as the Python app does; a native webview window comes later.
#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <winsock2.h>
#include <ws2tcpip.h>
#include <shellapi.h>
#include <windows.h>
using sock_t = SOCKET;
#define close_socket closesocket
#else
#include <netdb.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>
using sock_t = int;
#define INVALID_SOCKET (-1)
#define close_socket close
#endif

#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <map>
#include <mutex>
#include <sstream>
#include <string>
#include <thread>

#include "../core/config.hpp"
#include "../core/pipeline.hpp"

namespace fs = std::filesystem;
using cb::Json;

namespace {

fs::path ROOT, VIEWER, WORK, CONFIG;

std::mutex mu;                       // guards state and progress
Json state = [] { Json j = Json::object(); j["input"] = Json(); return j; }();
std::mutex busy;
std::atomic<bool> cancel{false};     // set by a newly opened page: the run for the page before it is abandoned
double p_fraction = 0, p_started = 0;
std::string p_stage;
bool p_running = false;

double now_s() {
    return std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count();
}

std::string read_file(const fs::path& p) {
    std::ifstream f(p, std::ios::binary);
    std::stringstream ss;
    ss << f.rdbuf();
    return ss.str();
}

int hexval(char c) {
    return c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1;
}

std::string unquote(const std::string& s, bool plus) {      // urllib unquote (plus: unquote_plus, as parse_qs)
    std::string out;
    for (size_t k = 0; k < s.size(); k++) {
        if (s[k] == '%' && k + 2 < s.size() && hexval(s[k + 1]) >= 0 && hexval(s[k + 2]) >= 0) {
            out += char(hexval(s[k + 1]) * 16 + hexval(s[k + 2]));
            k += 2;
        } else {
            out += plus && s[k] == '+' ? ' ' : s[k];
        }
    }
    return out;
}

std::string quote(const std::string& s) {      // urllib quote, '/' kept
    std::string out;
    for (unsigned char c : s) {
        if (std::isalnum(c) || c == '_' || c == '.' || c == '-' || c == '~' || c == '/') {
            out += char(c);
        } else {
            char b[4];
            std::snprintf(b, sizeof b, "%%%02X", c);
            out += b;
        }
    }
    return out;
}

std::map<std::string, std::string> parse_qs(const std::string& q) {     // first value of each key
    std::map<std::string, std::string> out;
    size_t start = 0;
    while (start <= q.size()) {
        size_t amp = q.find('&', start);
        std::string part = q.substr(start, amp == std::string::npos ? std::string::npos : amp - start);
        size_t eq = part.find('=');
        if (eq != std::string::npos && eq + 1 < part.size())      // parse_qs drops blank values
            out.emplace(unquote(part.substr(0, eq), true), unquote(part.substr(eq + 1), true));
        if (amp == std::string::npos) break;
        start = amp + 1;
    }
    return out;
}

std::string safe_name(const std::string& raw) {
    std::string name = unquote(raw, false);
    size_t cut = name.find_last_of("/\\");
    if (cut != std::string::npos) name = name.substr(cut + 1);
    if (name == ".") name.clear();
    for (auto& c : name) {       // [^\w.\- ] -> _ (bytes of non-ASCII characters kept, as \w keeps letters)
        unsigned char u = (unsigned char)c;
        if (!(u >= 0x80 || std::isalnum(u) || c == '_' || c == '.' || c == '-' || c == ' ')) c = '_';
    }
    size_t a = name.find_first_not_of(" \t"), b = name.find_last_not_of(" \t");
    name = a == std::string::npos ? "" : name.substr(a, b - a + 1);
    if (name.empty()) name = "upload.gcode";
    std::string lower = name;
    for (auto& c : lower) c = char(std::tolower((unsigned char)c));
    return lower.size() >= 6 && lower.compare(lower.size() - 6, 6, ".gcode") == 0 ? name : name + ".gcode";
}

Json config_payload() {
    Json cfg = cb::load_config(CONFIG);
    Json sections = Json::array();
    for (const auto& [name, defs] : cb::defaults().o) {
        Json fields = Json::array();
        for (const auto& [key, def] : defs.o) {
            std::string full = name + "." + key, label = key, unit, text;
            for (const auto& h : cb::help())
                if (full == h.key) label = h.label, unit = h.unit, text = h.text;
            const char* kind = def.type == Json::Bool ? "bool" : def.type == Json::Int ? "int" : def.type == Json::Str ? "str" : "float";
            const Json* have = cfg.get(name) ? cfg.get(name)->get(key) : nullptr;
            Json f = Json::object();
            f["key"] = key;
            f["label"] = label;
            f["unit"] = unit;
            f["help"] = text;
            f["type"] = kind;
            f["value"] = have ? *have : def;
            f["default"] = def;
            fields.a.push_back(f);
        }
        Json s = Json::object();
        s["name"] = name;
        s["fields"] = fields;
        sections.a.push_back(s);
    }
    Json out = Json::object();
    out["sections"] = sections;
    return out;
}

bool send_all(sock_t s, const std::string& data) {
    size_t sent = 0;
    while (sent < data.size()) {
        int n = send(s, data.data() + sent, int(std::min<size_t>(data.size() - sent, 1 << 20)), 0);
        if (n <= 0) return false;
        sent += size_t(n);
    }
    return true;
}

// Moonraker upload (POST /server/files/upload, root gcodes); starts the print only when asked
// ponytail: plain http only (Moonraker on a LAN); https would need a TLS library
Json send_to_printer(const fs::path& path, std::string host, bool start) {
    while (!host.empty() && (host.back() == '/' || host.back() == ' ')) host.pop_back();
    while (!host.empty() && host.front() == ' ') host.erase(0, 1);
    std::string url = (host.rfind("http", 0) == 0 ? host : "http://" + host) + "/server/files/upload";
    if (url.rfind("http://", 0) != 0) throw std::runtime_error("only http:// printer addresses are supported");
    std::string hostport = url.substr(7, url.find('/', 7) - 7), port = "80", name = hostport;
    if (size_t c = hostport.rfind(':'); c != std::string::npos) {
        name = hostport.substr(0, c);
        port = hostport.substr(c + 1);
    }
    const std::string boundary = "----colorbatching" + std::to_string(std::hash<std::string>()(path.filename().string()));
    std::string body;
    auto part = [&](const std::string& k, const std::string& v) {
        body += "--" + boundary + "\r\nContent-Disposition: form-data; name=\"" + k + "\"\r\n\r\n" + v + "\r\n";
    };
    part("root", "gcodes");
    if (start) part("print", "true");
    body += "--" + boundary + "\r\nContent-Disposition: form-data; name=\"file\"; filename=\"" + path.filename().string() +
            "\"\r\nContent-Type: application/octet-stream\r\n\r\n" + read_file(path) + "\r\n--" + boundary + "--\r\n";
    addrinfo hints{}, *res = nullptr;
    hints.ai_socktype = SOCK_STREAM;
    if (getaddrinfo(name.c_str(), port.c_str(), &hints, &res) != 0) throw std::runtime_error("cannot resolve " + name);
    sock_t s = INVALID_SOCKET;
    for (addrinfo* a = res; a; a = a->ai_next) {
        s = socket(a->ai_family, a->ai_socktype, a->ai_protocol);
        if (s == INVALID_SOCKET) continue;
        if (connect(s, a->ai_addr, int(a->ai_addrlen)) == 0) break;
        close_socket(s);
        s = INVALID_SOCKET;
    }
    freeaddrinfo(res);
    if (s == INVALID_SOCKET) throw std::runtime_error("cannot connect to " + hostport);
    std::string req = "POST /server/files/upload HTTP/1.0\r\nHost: " + hostport +
                      "\r\nContent-Type: multipart/form-data; boundary=" + boundary +
                      "\r\nContent-Length: " + std::to_string(body.size()) + "\r\n\r\n";
    bool ok = send_all(s, req) && send_all(s, body);
    std::string reply;
    char buf[65536];
    int n;
    while (ok && (n = recv(s, buf, sizeof buf, 0)) > 0) reply.append(buf, size_t(n));
    close_socket(s);
    size_t sp = reply.find(' ');
    int code = sp == std::string::npos ? 0 : std::atoi(reply.c_str() + sp + 1);
    if (code < 200 || code >= 300) throw std::runtime_error("HTTP Error " + std::to_string(code) + " from " + url);
    size_t split = reply.find("\r\n\r\n");
    std::string payload = split == std::string::npos ? "" : reply.substr(split + 4);
    Json out = Json::object();
    out["ok"] = true;
    out["url"] = url;
    out["printing"] = start;
    out["moonraker"] = payload.empty() ? Json::object() : Json::parse(payload);
    return out;
}

struct Reply {
    int code = 200;
    std::string body, type = "application/json";
    std::vector<std::pair<std::string, std::string>> extra;
};

Reply json_reply(int code, const Json& j) { return {code, j.dump()}; }
Reply error(int code, const std::string& msg) {
    Json j = Json::object();
    j["error"] = msg;
    return json_reply(code, j);
}

Reply get(const std::string& path, const std::map<std::string, std::string>& q) {
    if (path == "/") return {200, read_file(VIEWER), "text/html; charset=utf-8"};
    if (path == "/icon.svg" || path == "/icon.png")
        return {200, read_file(VIEWER.parent_path() / path.substr(1)), path == "/icon.svg" ? "image/svg+xml" : "image/png"};
    if (path == "/api/config") return json_reply(200, config_payload());
    if (path == "/api/state") {
        std::lock_guard<std::mutex> l(mu);
        return json_reply(200, state);
    }
    if (path == "/api/progress") {
        std::lock_guard<std::mutex> l(mu);
        double elapsed = p_running ? now_s() - p_started : 0.0;
        Json j = Json::object();
        j["running"] = p_running;
        j["fraction"] = p_fraction;
        j["stage"] = p_stage;
        j["elapsed"] = elapsed;
        j["left"] = p_running && p_fraction > 0.12 ? Json(elapsed * (1 - p_fraction) / p_fraction) : Json();
        return json_reply(200, j);
    }
    if (path.rfind("/work/", 0) == 0) {
        fs::path f = WORK / fs::u8path(safe_name(path.substr(6)));
        if (!fs::is_regular_file(f)) return error(404, "not found");
        Reply r{200, read_file(f), "text/plain; charset=utf-8"};
        if (q.count("download")) r.extra.push_back({"Content-Disposition", "attachment; filename=\"" + f.filename().u8string() + "\""});
        return r;
    }
    return error(404, "not found");
}

Reply post(const std::string& path, const std::map<std::string, std::string>& q, const std::string& body) {
    if (path == "/api/config") {
        Json cfg = cb::load_config(CONFIG);
        cb::save_config(cb::apply(cfg, Json::parse(body)), CONFIG);
        Json j = Json::object();
        j["ok"] = true;
        return json_reply(200, j);
    }
    if (path == "/api/cancel") {
        cancel = true;
        std::lock_guard<std::mutex> l(mu);
        Json j = Json::object();
        j["ok"] = true;
        j["was_running"] = p_running;
        return json_reply(200, j);
    }
    if (path == "/api/upload") {
        auto it = q.find("name");
        std::string name = safe_name(it == q.end() ? "upload.gcode" : it->second);
        fs::create_directories(WORK);
        std::ofstream(WORK / fs::u8path(name), std::ios::binary) << body;
        {
            std::lock_guard<std::mutex> l(mu);
            state["input"] = name;
        }
        Json j = Json::object();
        j["name"] = name;
        j["url"] = "/work/" + name;
        return json_reply(200, j);
    }
    if (path == "/api/send") {
        Json req = Json::parse(body);
        fs::path src = WORK / fs::u8path(safe_name(req.get("output")->s));
        if (!fs::is_regular_file(src)) return error(404, "process a file first");
        const Json* start = req.get("start");
        return json_reply(200, send_to_printer(src, req.get("host")->s, start && start->type == Json::Bool && start->b));
    }
    if (path == "/api/process") {
        Json req = Json::parse(body);
        std::string name = safe_name(req.get("input")->s);
        fs::path src = WORK / fs::u8path(name);
        if (!fs::is_regular_file(src)) return error(404, name + " is not uploaded");
        std::unique_lock<std::mutex> lock(busy, std::try_to_lock);
        if (!lock.owns_lock()) return error(409, "already processing");
        {
            std::lock_guard<std::mutex> l(mu);
            p_fraction = 0;
            p_stage.clear();
            p_started = now_s();
            p_running = true;
        }
        cancel = false;
        struct Done {
            ~Done() {
                std::lock_guard<std::mutex> l(mu);
                p_running = false;
            }
        } done;
        std::string out_name = name.substr(0, name.size() - 6) + ".color-batching.gcode";
        fs::path out = WORK / fs::u8path(out_name);
        Json cfg = cb::load_config(CONFIG);
        const Json* given = req.get("config");
        cb::apply(cfg, given ? *given : Json::object());
        Json result;
        try {
            result = cb::run(src.u8string(), out.u8string(), cfg,
                             [](double f, const std::string& stage) {
                                 std::lock_guard<std::mutex> l(mu);
                                 p_fraction = f;
                                 p_stage = stage;
                             },
                             [] { return cancel.load(); });
        } catch (const cb::Cancelled&) {
            return error(409, "cancelled");
        }
        result["output"] = out_name;
        result["input"] = name;
        return json_reply(200, result);
    }
    return error(404, "not found");
}

const char* reason(int code) {
    switch (code) {
        case 200: return "OK";
        case 404: return "Not Found";
        case 409: return "Conflict";
        default: return "Internal Server Error";
    }
}

void serve(sock_t c) {
    std::string data;
    char buf[65536];
    size_t head_end;
    while ((head_end = data.find("\r\n\r\n")) == std::string::npos) {
        int n = recv(c, buf, sizeof buf, 0);
        if (n <= 0) {
            close_socket(c);
            return;
        }
        data.append(buf, size_t(n));
    }
    std::string head = data.substr(0, head_end);
    size_t length = 0;
    {
        std::istringstream h(head);
        std::string line;
        std::getline(h, line);
        while (std::getline(h, line)) {
            std::string lower = line;
            for (auto& ch : lower) ch = char(std::tolower((unsigned char)ch));
            if (lower.rfind("content-length:", 0) == 0) length = std::stoull(line.substr(15));
        }
    }
    std::string body = data.substr(head_end + 4);
    while (body.size() < length) {
        int n = recv(c, buf, int(std::min<size_t>(sizeof buf, length - body.size())), 0);
        if (n <= 0) break;
        body.append(buf, size_t(n));
    }
    std::istringstream first(head);
    std::string method, target;
    first >> method >> target;
    size_t qm = target.find('?');
    std::string path = target.substr(0, qm), query = qm == std::string::npos ? "" : target.substr(qm + 1);
    Reply r;
    try {
        auto q = parse_qs(query);
        r = method == "GET" ? get(path, q) : method == "POST" ? post(path, q, body) : error(404, "not found");
    } catch (const std::exception& e) {
        r = error(500, std::string("Error: ") + e.what());
    }
    std::string out = "HTTP/1.0 " + std::to_string(r.code) + " " + reason(r.code) + "\r\nContent-Type: " + r.type +
                      "\r\nContent-Length: " + std::to_string(r.body.size()) + "\r\nCache-Control: no-store\r\n";
    for (auto& [k, v] : r.extra) out += k + ": " + v + "\r\n";
    out += "\r\n";
    send_all(c, out) && send_all(c, r.body);
    close_socket(c);
}

void open_browser(const std::string& url) {
#ifdef _WIN32
    ShellExecuteA(nullptr, "open", url.c_str(), nullptr, nullptr, SW_SHOWNORMAL);
#elif defined(__APPLE__)
    std::system(("open '" + url + "'").c_str());
#else
    std::system(("xdg-open '" + url + "' >/dev/null 2>&1 &").c_str());
#endif
}

}  // namespace

int main(int argc, char** argv) {
    std::string file;
    int port = 8765;
    bool browser = true;
    for (int k = 1; k < argc; k++) {
        std::string a = argv[k];
        if (a == "--port" && k + 1 < argc) port = std::atoi(argv[++k]);
        else if (a == "--no-browser") browser = false;
        else if (file.empty() && !a.empty() && a[0] != '-') file = a;
        else {
            std::fprintf(stderr, "usage: color-batching-app [file] [--port PORT] [--no-browser]\n");
            return 2;
        }
    }
    ROOT = cb::find_root(fs::absolute(fs::path(argv[0])).parent_path());
    VIEWER = ROOT / "viewer" / "index.html";
    WORK = ROOT / "work";
    CONFIG = cb::config_path(ROOT);
    fs::create_directories(WORK);
    if (!file.empty()) {
        std::string name = safe_name(fs::path(file).filename().u8string());
        fs::path dst = WORK / fs::u8path(name);
        if (fs::weakly_canonical(file) != fs::weakly_canonical(dst)) fs::copy_file(file, dst, fs::copy_options::overwrite_existing);
        state["input"] = name;
    }
#ifdef _WIN32
    WSADATA wsa;
    WSAStartup(MAKEWORD(2, 2), &wsa);
#endif
    sock_t srv = socket(AF_INET, SOCK_STREAM, 0);
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(uint16_t(port));
    addr.sin_addr.s_addr = htonl(0x7F000001);      // 127.0.0.1
    if (bind(srv, (sockaddr*)&addr, sizeof addr) != 0 || listen(srv, 16) != 0) {
        std::fprintf(stderr, "cannot listen on 127.0.0.1:%d\n", port);
        return 1;
    }
    std::string url = "http://127.0.0.1:" + std::to_string(port) + "/";
    if (!file.empty()) url += "?input=" + quote(state.get("input")->s);     // a refresh starts empty
    if (browser) open_browser(url);
    std::printf("Color Batching app at %s  (Ctrl+C to stop)\n", url.c_str());
    std::fflush(stdout);
    while (true) {
        sock_t c = accept(srv, nullptr, nullptr);
        if (c == INVALID_SOCKET) continue;
        std::thread(serve, c).detach();
    }
}
