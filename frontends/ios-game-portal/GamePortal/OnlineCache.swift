import Foundation

/// Durable, account-scoped snapshots. Credentials remain in Keychain.
/// Match state is opaque JSON; game packages are pinned by game ID and version.
final class OnlineCache {
    private let file: URL
    private var contents: [String: Any]

    init(directory: URL, accountID: String) throws {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let name = Data(accountID.utf8).base64EncodedString()
            .replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "+", with: "-")
        file = directory.appendingPathComponent("account-\(name).json")
        if FileManager.default.fileExists(atPath: file.path) {
            let data = try Data(contentsOf: file)
            guard let json = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                  json["format"] as? Int == 1,
                  json["account"] as? String == accountID,
                  json["games"] is [[String: Any]], json["matches"] is [[String: Any]],
                  json["packages"] is [String: String] else { throw CocoaError(.fileReadCorruptFile) }
            if let pending = json["pending"] {
                guard let queues = pending as? [String: [[String: Any]]], queues.values.allSatisfy({ entries in
                    entries.allSatisfy { $0["id"] is String && $0["base"] is [String: Any] && $0["result"] is [String: Any] && $0["body"] is [String: Any] }
                }) else { throw CocoaError(.fileReadCorruptFile) }
            }
            if let conflicts = json["conflicts"], !(conflicts is [String: String]) { throw CocoaError(.fileReadCorruptFile) }
            contents = json
        } else {
            contents = ["format": 1, "account": accountID, "games": [[String: Any]](),
                        "matches": [[String: Any]](), "packages": [String: String]()]
        }
    }
    var games: [[String: Any]] { contents["games"] as? [[String: Any]] ?? [] }
    var matches: [[String: Any]] { contents["matches"] as? [[String: Any]] ?? [] }
    var pending: [String: [[String: Any]]] { contents["pending"] as? [String: [[String: Any]]] ?? [:] }
    var conflicts: [String: String] { contents["conflicts"] as? [String: String] ?? [:] }
    func enqueue(match: [String: Any], result: [String: Any], body: [String: Any]) throws {
        guard let id = match["id"] as? String else { throw CocoaError(.coderInvalidValue) }
        var next = contents
        var queues = pending
        queues[id, default: []].append(["id": UUID().uuidString, "base": match, "result": result, "body": body])
        next["pending"] = queues
        var snapshots = matches.filter { $0["id"] as? String != id }
        snapshots.insert(result, at: 0); next["matches"] = snapshots
        try write(next)
    }
    func acknowledge(matchID: String, entryID: String, server: [String: Any]) throws {
        var queues = pending
        guard queues[matchID]?.first?["id"] as? String == entryID else { throw CocoaError(.coderInvalidValue) }
        queues[matchID]?.removeFirst()
        if queues[matchID]?.isEmpty == true { queues.removeValue(forKey: matchID) }
        var next = contents; next["pending"] = queues
        if queues[matchID] == nil {
            next["matches"] = [server] + matches.filter { $0["id"] as? String != matchID }
        }
        try write(next)
    }
    func markConflict(_ id: String, message: String) throws {
        var next = contents; var problems = conflicts
        problems[id] = message; next["conflicts"] = problems; try write(next)
    }
    func resolveConflict(_ id: String, server: [String: Any]) throws {
        // Preserve the user's abandoned branch for recovery instead of silently losing it.
        var next = contents
        var archives = contents["archives"] as? [[String: Any]] ?? []
        archives.append(["match_id": id, "moves": pending[id] ?? [], "saved_at": Date().timeIntervalSince1970])
        next["archives"] = archives
        var queues = pending; queues.removeValue(forKey: id); next["pending"] = queues
        var problems = conflicts; problems.removeValue(forKey: id); next["conflicts"] = problems
        next["matches"] = [server] + matches.filter { $0["id"] as? String != id }
        try write(next)
    }
    func code(gameID: String, version: Int) -> String? {
        (contents["packages"] as? [String: String])?[Self.packageKey(gameID, version)]
    }
    private static func packageKey(_ id: String, _ version: Int) -> String {
        // Encode the ID so delimiter characters cannot cause collisions.
        "\(Data(id.utf8).base64EncodedString()):\(version)"
    }
    private func write(_ next: [String: Any]) throws {
        let data = try JSONSerialization.data(withJSONObject: next, options: [.sortedKeys])
        try data.write(to: file, options: .atomic)
        contents = next
    }
    func saveGames(_ games: [[String: Any]]) throws {
        var next = contents
        next["games"] = games.map { game in
            var metadata = game
            metadata.removeValue(forKey: "code")
            return metadata
        }
        try write(next)
    }
    func saveMatches(_ matches: [[String: Any]]) throws {
        var next = contents; next["matches"] = matches
        try write(next)
    }
    func saveMatch(_ match: [String: Any]) throws {
        guard let id = match["id"] as? String, !id.isEmpty else { throw CocoaError(.coderInvalidValue) }
        var saved = matches
        if let index = saved.firstIndex(where: { $0["id"] as? String == id }) { saved[index] = match }
        else { saved.insert(match, at: 0) }
        try saveMatches(saved)
    }
    func saveCode(_ code: String, gameID: String, version: Int) throws {
        guard !code.isEmpty else { throw CocoaError(.coderInvalidValue) }
        var next = contents
        var packages = contents["packages"] as? [String: String] ?? [:]
        packages[Self.packageKey(gameID, version)] = code
        next["packages"] = packages
        try write(next)
    }
}
