import Foundation

/// The guest account the server gave us. Saved in the Keychain, never in a file.
struct Account: Codable, Equatable {
    let id: String
    let password: String
    let displayName: String
}

/// One game from `GET /games` or `GET /builder/games`. Fields we don't use (dates, etc.)
/// are ignored.
struct Game: Codable, Identifiable, Hashable {
    let id: String
    let version: Int
    let ownerUserId: String
    let name: String
    let description: String
    let allowedPlayerCounts: [Int]
    let allowsLeaveMidMatch: Bool
    let allowsJoinMidMatch: Bool
    let code: String
    /// false: only its owner sees it (unpublished from the web builder). Missing in games
    /// saved before the server had this field, which were all published.
    var published: Bool? = nil

    var isPublished: Bool { published != false }

    var fields: GameFields {
        GameFields(name: name, description: description, allowedPlayerCounts: allowedPlayerCounts, code: code)
    }

    /// This game with the fields edited on the phone.
    func with(_ f: GameFields) -> Game {
        Game(id: id, version: version, ownerUserId: ownerUserId, name: f.name, description: f.description,
             allowedPlayerCounts: f.allowedPlayerCounts, allowsLeaveMidMatch: allowsLeaveMidMatch,
             allowsJoinMidMatch: allowsJoinMidMatch, code: f.code, published: published)
    }
}

/// The game fields the editor changes (body of `POST /games` and `PATCH /games/{id}`).
struct GameFields: Codable, Equatable {
    var name: String
    var description: String
    var allowedPlayerCounts: [Int]
    var code: String
}

enum APIError: LocalizedError {
    case server(status: Int, detail: String)

    var errorDescription: String? {
        switch self {
        case .server(let status, let detail): "Server error \(status): \(detail)"
        }
    }

    var status: Int {
        switch self {
        case .server(let status, _): status
        }
    }
}

/// Talks to the buildplay.fun REST API (see "API" in the root README).
enum API {
    static let liveURL = URL(string: "https://buildplay.fun")!

    /// The server. Debug builds can use another one, e.g. a backend running on the Mac:
    /// launch argument `-server http://localhost:8000`.
    static let baseURL: URL = {
        #if DEBUG
        if let s = UserDefaults.standard.string(forKey: "server"), let url = URL(string: s) { return url }
        #endif
        return liveURL
    }()

    /// Keeps the account, games and queued edits of another server apart from buildplay.fun's.
    static var storageName: String {
        baseURL == liveURL ? "buildplay.fun" : "\(baseURL.host() ?? "server")-\(baseURL.port ?? 0)"
    }

    static let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()

    static let encoder: JSONEncoder = {
        let e = JSONEncoder()
        e.keyEncodingStrategy = .convertToSnakeCase
        return e
    }()

    /// `POST /users {display_name}`: creates a guest and returns its id and password.
    static func createGuest(displayName: String) async throws -> Account {
        struct UserWithPassword: Decodable { let id: String; let password: String; let displayName: String }
        let body = try encoder.encode(["display_name": displayName])
        let user = try decoder.decode(UserWithPassword.self, from: try await send("POST", "users", body: body))
        return Account(id: user.id, password: user.password, displayName: user.displayName)
    }

    /// `POST /auth/google`: logs in with a Google ID token. The server checks the token with
    /// Google and returns this person's account (created on the first login).
    static func logInWithGoogle(idToken: String) async throws -> Account {
        struct AuthResponse: Decodable { let userId: String; let password: String; let displayName: String }
        let body = try encoder.encode(["id_token": idToken])
        let auth = try decoder.decode(AuthResponse.self, from: try await send("POST", "auth/google", body: body))
        return Account(id: auth.userId, password: auth.password, displayName: auth.displayName)
    }

    /// `GET /games`: every published game that isn't deleted.
    static func fetchGames() async throws -> [Game] {
        try decoder.decode([Game].self, from: try await send("GET", "games"))
    }

    /// `GET /builder/games`: my own games, including unpublished ones.
    static func fetchMyGames(as account: Account) async throws -> [Game] {
        try decoder.decode([Game].self, from: try await send("GET", "builder/games", as: account))
    }

    /// `POST /games`: creates a game owned by `account`.
    static func createGame(_ fields: GameFields, as account: Account) async throws -> Game {
        try decoder.decode(Game.self, from: try await send("POST", "games", body: try encoder.encode(fields), as: account))
    }

    /// `PATCH /games/{id}`: saves the fields as a new version (creator only).
    static func updateGame(id: String, _ fields: GameFields, as account: Account) async throws -> Game {
        try decoder.decode(Game.self, from: try await send("PATCH", "games/\(id)", body: try encoder.encode(fields), as: account))
    }

    /// `DELETE /games/{id}` (creator only).
    static func deleteGame(id: String, as account: Account) async throws {
        _ = try await send("DELETE", "games/\(id)", as: account)
    }

    fileprivate static func send(_ method: String, _ path: String, body: Data? = nil, as account: Account? = nil) async throws -> Data {
        var request = URLRequest(url: baseURL.appending(path: path))
        request.httpMethod = method
        request.timeoutInterval = 15
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        if let account {
            request.setValue(account.id, forHTTPHeaderField: "X-User-Id")
            request.setValue(account.password, forHTTPHeaderField: "X-User-Password")
        }
        #if DEBUG
        // For testing airplane mode on the simulator: `-simulateOffline` fails every request.
        if ProcessInfo.processInfo.arguments.contains("-simulateOffline") { throw URLError(.notConnectedToInternet) }
        #endif
        let (data, response) = try await URLSession.shared.data(for: request)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(status) else {
            struct ErrorBody: Decodable { let detail: String }
            let detail = (try? JSONDecoder().decode(ErrorBody.self, from: data))?.detail
                ?? String(decoding: data, as: UTF8.self)
            throw APIError.server(status: status, detail: detail)
        }
        return data
    }
}

// MARK: - Matches

/// A match from the server (`MatchOut` in the backend's schemas.py).
struct Match: Codable, Identifiable, Equatable {
    struct Seat: Codable, Equatable {
        let playerIndex: Int
        let kind: String        // "human" or "computer"
        let userId: String?
    }

    let id: String
    let gameId: String
    let gameVersion: Int
    let ownerUserId: String
    var status: String          // "waiting_for_players", "ongoing" or "over"
    var endReason: String?
    var players: [Seat]
    var turnOfPlayerIndices: [Int]?
    var state: JSONValue
    var moveCount: Int

    var isWaiting: Bool { status == "waiting_for_players" }
    var isOngoing: Bool { status == "ongoing" }
    var isOver: Bool { status == "over" }
    func seat(of userId: String?) -> Seat? { players.first { $0.userId != nil && $0.userId == userId } }
}

extension API {
    /// `POST /matches`: the caller is player 0; `computers` computer seats follow.
    static func createMatch(gameId: String, computers: Int, as account: Account) async throws -> Match {
        let body = try encoder.encode(["game_id": JSONValue.string(gameId), "num_computer_opponents": .number(Double(computers))])
        return try decoder.decode(Match.self, from: try await send("POST", "matches", body: body, as: account))
    }

    /// `GET /matches`: my matches (owned or seated).
    static func myMatches(as account: Account) async throws -> [Match] {
        try decoder.decode([Match].self, from: try await send("GET", "matches", as: account))
    }

    /// `GET /matches/open`: matches anyone may join.
    static func openMatches() async throws -> [Match] {
        try decoder.decode([Match].self, from: try await send("GET", "matches/open"))
    }

    /// Sends my login (when there is one): a match of an unpublished game is only visible to
    /// its players and the game's owner.
    static func match(id: String, as account: Account?) async throws -> Match {
        try decoder.decode(Match.self, from: try await send("GET", "matches/\(id)", as: account))
    }

    static func joinMatch(id: String, as account: Account) async throws -> Match {
        try decoder.decode(Match.self, from: try await send("POST", "matches/\(id)/join", as: account))
    }

    /// `POST /matches/{id}/start` with the defaults: seat 0 moves first, no initial state.
    static func startMatch(id: String, as account: Account) async throws -> Match {
        try decoder.decode(Match.self, from: try await send("POST", "matches/\(id)/start", body: Data("{}".utf8), as: account))
    }

    /// `PATCH /matches/{id}`: number of computer seats, before the start (owner only).
    static func setComputers(matchId: String, _ computers: Int, as account: Account) async throws -> Match {
        let body = try encoder.encode(["num_computer_opponents": computers])
        return try decoder.decode(Match.self, from: try await send("PATCH", "matches/\(matchId)", body: body, as: account))
    }

    /// `DELETE /matches/{id}`: the owner deletes the match for everyone; another player hides
    /// a finished match from their own list.
    static func deleteMatch(id: String, as account: Account) async throws {
        _ = try await send("DELETE", "matches/\(id)", as: account)
    }

    /// `POST /matches/{id}/leave`: in an ongoing match a computer takes the seat if the game
    /// allows leaving, otherwise the match ends.
    static func leaveMatch(id: String, as account: Account) async throws {
        _ = try await send("POST", "matches/\(id)/leave", as: account)
    }

    /// `POST /matches/{id}/moves`. `expectedMoveCount` makes the server reject a stale move.
    static func makeMove(matchId: String, newState: JSONValue, nextTurn: [Int]?, expectedMoveCount: Int,
                         as account: Account) async throws -> Match {
        let body: JSONValue = .object([
            "new_state": newState,
            // required field: null (not missing) ends the match
            "next_turn_player_indices": nextTurn.map { .array($0.map { .number(Double($0)) }) } ?? .null,
            "expected_move_count": .number(Double(expectedMoveCount)),
        ])
        return try decoder.decode(Match.self, from: try await send("POST", "matches/\(matchId)/moves",
                                                                   body: try JSONEncoder().encode(body), as: account))
    }

    /// `GET /users/{id}`: a player's display name.
    static func displayName(userId: String) async throws -> String {
        struct User: Decodable { let displayName: String }
        return try decoder.decode(User.self, from: try await send("GET", "users/\(userId)")).displayName
    }

    /// `GET /games/{id}/versions/{version}`: the exact version a match plays. Sends my login,
    /// so my own unpublished games (and versions my matches use) still load.
    static func gameVersion(id: String, version: Int, as account: Account?) async throws -> Game {
        try decoder.decode(Game.self, from: try await send("GET", "games/\(id)/versions/\(version)", as: account))
    }

    /// `GET /games/{id}`: the latest version, with my login (see `gameVersion`).
    static func game(id: String, as account: Account?) async throws -> Game {
        try decoder.decode(Game.self, from: try await send("GET", "games/\(id)", as: account))
    }

    /// The web portal's link to a match (it works in any browser).
    static func shareURL(matchId: String) -> URL {
        URL(string: "\(baseURL.absoluteString)/portal#match=\(matchId)")!
    }
}
