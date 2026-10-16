import Foundation

/// Files on the phone: the last games list (each game with its `code`, so it plays
/// offline) and the queue of edits not uploaded yet.
enum GameCache {
    private static let folder = URL.applicationSupportDirectory
    // buildplay.fun keeps the original file names; other (debug) servers get their own files.
    private static var suffix: String { API.baseURL == API.liveURL ? "" : "-\(API.storageName)" }
    private static var gamesFile: URL { folder.appending(path: "games\(suffix).json") }
    private static var queueFile: URL { folder.appending(path: "pending-edits\(suffix).json") }

    static func load() -> (games: [Game], savedAt: Date)? {
        guard let data = try? Data(contentsOf: gamesFile),
              let games = try? API.decoder.decode([Game].self, from: data) else { return nil }
        let savedAt = (try? gamesFile.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate
        return (games, savedAt ?? .distantPast)
    }

    static func save(_ games: [Game]) {
        write(games, to: gamesFile)
    }

    static func loadQueue() -> [PendingEdit] {
        guard let data = try? Data(contentsOf: queueFile) else { return [] }
        return (try? API.decoder.decode([PendingEdit].self, from: data)) ?? []
    }

    static func saveQueue(_ queue: [PendingEdit]) {
        write(queue, to: queueFile)
    }

    /// Any other saved data (matches, game versions, names, queued moves), by name.
    static func load<T: Decodable>(_ name: String, as type: T.Type) -> T? {
        guard let data = try? Data(contentsOf: file(name)) else { return nil }
        return try? API.decoder.decode(T.self, from: data)
    }

    static func save(_ value: some Encodable, as name: String) {
        write(value, to: file(name))
    }

    private static func file(_ name: String) -> URL { folder.appending(path: "\(name)\(suffix).json") }

    /// Deletes this server's files (debug `-resetLocalData`).
    static func reset() {
        for url in [gamesFile, queueFile] + ["matches", "game-versions", "user-names", "pending-moves"].map(file) {
            try? FileManager.default.removeItem(at: url)
        }
    }

    private static func write(_ value: some Encodable, to file: URL) {
        do {
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            try API.encoder.encode(value).write(to: file, options: .atomic)
        } catch {
            print("Could not save \(file.lastPathComponent): \(error)")
        }
    }
}

/// One change made on the phone that the server hasn't received yet.
struct PendingEdit: Codable, Identifiable, Equatable {
    enum Kind: String, Codable { case create, update, delete }
    var id = UUID()
    let kind: Kind
    /// For a game created offline, a temporary "local-..." id until the server gives it a real one.
    var gameId: String
    var fields: GameFields?
}
