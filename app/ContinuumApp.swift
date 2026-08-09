import SwiftUI
import AppKit

// MARK: - Model

/// One CLI session, as reported by the watcher's state file.
struct Session: Identifiable, Equatable {
    let id: String          // pane key, e.g. "term:/dev/ttys004" or "tmux:%3"
    let label: String       // short pane name
    let cwd: String
    let status: String      // active | waiting | probing | gave_up | lost
    let resetAt: Date?
    let lastSeen: Date
    let hasContinuum: Bool

    var project: String {
        let name = (cwd as NSString).lastPathComponent
        return name.isEmpty ? label : name
    }

    /// The watcher saw this pane within the last few minutes.
    var isLive: Bool { Date().timeIntervalSince(lastSeen) < 180 }
}

// MARK: - Locations

enum Paths {
    /// Mirrors the CLI: XDG_STATE_HOME when set, otherwise ~/.local/state.
    static let stateDir: URL = {
        let home = FileManager.default.homeDirectoryForCurrentUser
        if let xdg = ProcessInfo.processInfo.environment["XDG_STATE_HOME"], !xdg.isEmpty {
            return URL(fileURLWithPath: xdg).appendingPathComponent("continuum")
        }
        return home.appendingPathComponent(".local/state/continuum")
    }()

    static var state: URL { stateDir.appendingPathComponent("state.json") }
    static var enabled: URL { stateDir.appendingPathComponent("enabled.json") }
    static var pid: URL { stateDir.appendingPathComponent("continuum.pid") }
    /// Written by the watcher so the app can find the CLI wherever it was cloned.
    static var install: URL { stateDir.appendingPathComponent("install.json") }
}

// MARK: - Store

@MainActor
final class Store: ObservableObject {
    @Published private(set) var sessions: [Session] = []
    @Published private(set) var watcherRunning = false
    /// Project path -> resume it or not. Keyed by path rather than pane, so
    /// reopening the same project in a new tab keeps the choice.
    @Published var enabled: [String: Bool] = [:]

    private var timer: Timer?

    init() {
        loadEnabled()
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 3.0, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
    }

    func refresh() {
        watcherRunning = checkWatcher()
        sessions = readSessions()
    }

    private func checkWatcher() -> Bool {
        guard let text = try? String(contentsOf: Paths.pid, encoding: .utf8),
              let pid = Int32(text.trimmingCharacters(in: .whitespacesAndNewlines))
        else { return false }
        return kill(pid, 0) == 0
    }

    private func readSessions() -> [Session] {
        guard let data = try? Data(contentsOf: Paths.state),
              let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return [] }

        let fm = FileManager.default
        var out: [Session] = []
        for (key, raw) in root {
            guard let e = raw as? [String: Any] else { continue }
            let cwd = e["cwd"] as? String ?? ""
            let s = Session(
                id: key,
                label: e["label"] as? String ?? key,
                cwd: cwd,
                status: e["status"] as? String ?? "active",
                resetAt: (e["reset_at"] as? Double).map { Date(timeIntervalSince1970: $0) },
                lastSeen: Date(timeIntervalSince1970: e["last_seen"] as? Double ?? 0),
                hasContinuum: !cwd.isEmpty && fm.fileExists(atPath: cwd + "/.continuum/STATE.md")
            )
            if s.isLive { out.append(s) }
        }
        return out.sorted { $0.project.localizedStandardCompare($1.project) == .orderedAscending }
    }

    // MARK: Selection

    /// Resume this session? An explicit choice wins; otherwise projects with a
    /// .continuum plan default to on.
    func isOn(_ s: Session) -> Bool { enabled[s.cwd] ?? s.hasContinuum }

    func setOn(_ s: Session, _ value: Bool) {
        enabled[s.cwd] = value
        saveEnabled()
    }

    private func loadEnabled() {
        guard let data = try? Data(contentsOf: Paths.enabled),
              let dict = try? JSONSerialization.jsonObject(with: data) as? [String: Bool]
        else { return }
        enabled = dict
    }

    private func saveEnabled() {
        try? FileManager.default.createDirectory(at: Paths.stateDir,
                                                 withIntermediateDirectories: true)
        guard let data = try? JSONSerialization.data(withJSONObject: enabled,
                                                     options: [.prettyPrinted, .sortedKeys])
        else { return }
        try? data.write(to: Paths.enabled, options: .atomic)
    }

    // MARK: Watcher

    /// Reads install.json rather than assuming where the repo lives.
    func startWatcher() {
        var python = "/usr/bin/python3"
        var script = ""
        if let data = try? Data(contentsOf: Paths.install),
           let info = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            python = info["python"] as? String ?? python
            script = info["script"] as? String ?? ""
        }
        guard !script.isEmpty, FileManager.default.fileExists(atPath: script) else { return }

        let p = Process()
        p.executableURL = URL(fileURLWithPath: python)
        p.arguments = [script, "start"]
        try? p.run()
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in
            Task { @MainActor in self?.refresh() }
        }
    }

    var canStartWatcher: Bool {
        FileManager.default.fileExists(atPath: Paths.install.path)
    }
}

// MARK: - Views

struct ContentView: View {
    @StateObject private var store = Store()

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            if !store.watcherRunning { watcherWarning }
            if store.sessions.isEmpty {
                empty
            } else {
                List {
                    ForEach(store.sessions) { s in
                        SessionRow(session: s,
                                   isOn: Binding(get: { store.isOn(s) },
                                                 set: { store.setOn(s, $0) }))
                        .listRowInsets(EdgeInsets(top: 5, leading: 10, bottom: 5, trailing: 10))
                    }
                }
                .listStyle(.inset)
                .scrollContentBackground(.hidden)
            }
            Divider()
            footer
        }
        .frame(minWidth: 260, idealWidth: 300, minHeight: 260, idealHeight: 400)
        .background(.regularMaterial)
    }

    private var header: some View {
        HStack(spacing: 7) {
            Image(systemName: "arrow.trianglehead.clockwise")
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(.tint)
            Text("Continuum")
                .font(.system(size: 13, weight: .semibold))
            Spacer()
            Text("\(store.sessions.filter { store.isOn($0) }.count)/\(store.sessions.count)")
                .font(.system(size: 11, design: .rounded))
                .foregroundStyle(.secondary)
                .help("Sessions that will be resumed when their limit resets")
        }
        .padding(.horizontal, 12)
        .padding(.top, 10)
        .padding(.bottom, 8)
    }

    private var watcherWarning: some View {
        HStack(spacing: 8) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(.orange)
                .font(.system(size: 11))
            Text("Watcher not running")
                .font(.system(size: 11))
            Spacer()
            if store.canStartWatcher {
                Button("Start") { store.startWatcher() }
                    .controlSize(.small)
                    .buttonStyle(.borderedProminent)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 7)
        .background(Color.orange.opacity(0.10))
    }

    private var empty: some View {
        VStack(spacing: 6) {
            Spacer()
            Image(systemName: "terminal")
                .font(.system(size: 22))
                .foregroundStyle(.tertiary)
            Text("No sessions open")
                .font(.system(size: 12))
                .foregroundStyle(.secondary)
            Spacer()
        }
        .frame(maxWidth: .infinity)
    }

    private var footer: some View {
        HStack {
            Text("Ticked sessions resume when their limit resets")
                .font(.system(size: 10))
                .foregroundStyle(.secondary)
                .lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 4)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
    }
}

struct SessionRow: View {
    let session: Session
    @Binding var isOn: Bool

    var body: some View {
        Toggle(isOn: $isOn) {
            HStack(spacing: 6) {
                VStack(alignment: .leading, spacing: 1) {
                    Text(session.project)
                        .font(.system(size: 12))
                        .lineLimit(1)
                        .truncationMode(.middle)
                    Text(statusText)
                        .font(.system(size: 10))
                        .foregroundStyle(statusColor)
                        .lineLimit(1)
                }
                Spacer(minLength: 0)
                if session.hasContinuum {
                    Image(systemName: "bookmark.fill")
                        .font(.system(size: 8))
                        .foregroundStyle(.tint)
                        .help("This project has a .continuum plan")
                }
            }
        }
        .toggleStyle(.checkbox)
        .help(session.cwd)
    }

    private var statusText: String {
        switch session.status {
        case "waiting":
            guard let r = session.resetAt else { return "at limit" }
            let f = DateFormatter()
            f.dateFormat = "HH:mm"
            return r > Date() ? "at limit · resumes \(f.string(from: r))"
                              : "at limit · resuming"
        case "probing": return "resume sent"
        case "gave_up": return "could not resume"
        case "lost":    return "pane gone"
        default:        return session.label
        }
    }

    private var statusColor: Color {
        switch session.status {
        case "waiting", "probing": return .orange
        case "gave_up", "lost":    return .red
        default:                   return .secondary
        }
    }
}

// MARK: - App

/// Closing the window quits the app. Choices are on disk and the watcher is a
/// separate process, so resuming carries on regardless.
final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

@main
struct ContinuumApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate

    var body: some Scene {
        Window("Continuum", id: "main") {
            ContentView()
        }
        .windowStyle(.hiddenTitleBar)
        .windowResizability(.contentSize)
        .defaultSize(width: 300, height: 400)
        .commands { CommandGroup(replacing: .newItem) {} }
    }
}
