import Foundation

/// A test match played only on this phone (nothing is sent to the server).
/// It plays the platform's part of the Game API (root README, "Game API"): it builds the
/// `state_changed` messages and checks and applies the game's `make_move` messages.
struct LocalMatch {
    enum Kind: String, Codable { case human, computer }

    struct Player: Encodable {
        let playerIndex: Int
        let kind: Kind
        let name: String
    }

    struct Move {
        let state: JSONValue
        let nextTurn: [Int]?   // nil ends the match
    }

    let players: [Player]
    private(set) var moves: [Move] = []
    /// The human holding the phone. The game shows this seat's view (e.g. their cards).
    private(set) var viewerSeat: Int?
    /// Set when the phone must be passed to another human before their turn is shown.
    private(set) var handoffTo: Int?

    init(kinds: [Kind]) {
        players = kinds.enumerated().map { seat, kind in
            Player(playerIndex: seat, kind: kind, name: "\(kind == .human ? "Player" : "Computer") \(seat + 1)")
        }
        restart()
    }

    // MARK: Current position

    var state: JSONValue { moves.last?.state ?? .null }
    /// Seats that may move next; nil once the match is over. The first move belongs to seat 0.
    var turn: [Int]? { moves.isEmpty ? [0] : moves.last!.nextTurn }
    var isOver: Bool { turn == nil }
    /// The seat the game should move for: the first seat of the turn (like the web builder).
    var actingSeat: Int? { turn?.first }
    var humanSeats: [Int] { players.filter { $0.kind == .human }.map(\.playerIndex) }

    /// The message to send the game now. While a handoff is pending, nothing is sent, so
    /// the next player's private view (e.g. poker cards) isn't drawn behind the cover.
    var stateChanged: StateChanged? {
        guard handoffTo == nil else { return nil }
        return StateChanged(
            state: state,
            players: players,
            turnOfPlayerIndices: turn,
            turnOfPlayerIndex: turn?.first,
            status: isOver ? "over" : "ongoing",
            endReason: isOver ? "finished" : nil,
            moveCount: moves.count,
            myPlayerIndex: viewerSeat,
            actingForPlayerIndex: actingSeat)
    }

    // MARK: Changes

    /// Applies a `make_move` from the game. Returns an error message if it's rejected.
    mutating func apply(newState: JSONValue, nextTurn: [Int]?) -> String? {
        guard !isOver else { return "The game sent a move, but the match is over." }
        if let next = nextTurn {
            guard !next.isEmpty, Set(next).count == next.count,
                  next.allSatisfy({ players.indices.contains($0) }) else {
                return "The game sent next_turn_player_indices \(next), which isn't a set of seats."
            }
        }
        moves.append(Move(state: newState, nextTurn: nextTurn))
        updateViewer()
        return nil
    }

    /// Takes back moves until it's a human's turn again (so a computer doesn't just replay).
    mutating func undo() {
        guard !moves.isEmpty else { return }
        repeat { moves.removeLast() } while !moves.isEmpty && isComputer(actingSeat)
        updateViewer()
    }

    mutating func restart() {
        moves = []
        viewerSeat = humanSeats.first
        handoffTo = nil
        updateViewer()
    }

    /// The next player tapped "I'm ready".
    mutating func confirmHandoff() {
        guard let seat = handoffTo else { return }
        viewerSeat = seat
        handoffTo = nil
    }

    private func isComputer(_ seat: Int?) -> Bool { seat.map { players[$0].kind == .computer } ?? false }

    /// When a different human has the turn, ask to pass the phone first.
    private mutating func updateViewer() {
        handoffTo = nil
        guard let seat = actingSeat, players[seat].kind == .human, seat != viewerSeat else { return }
        if viewerSeat == nil { viewerSeat = seat } else { handoffTo = seat }
    }
}

/// The `state_changed` message (see "Game API" in the root README).
struct StateChanged: Encodable {
    var type = "state_changed"
    let state: JSONValue
    let players: [LocalMatch.Player]
    let turnOfPlayerIndices: [Int]?
    let turnOfPlayerIndex: Int?   // legacy field for older games
    let status: String
    let endReason: String?
    let moveCount: Int
    let myPlayerIndex: Int?
    let actingForPlayerIndex: Int?

    /// JSON with snake_case keys and explicit nulls, as games expect.
    func json() -> String {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        encoder.outputFormatting = .sortedKeys
        return String(decoding: (try? encoder.encode(self)) ?? Data("{}".utf8), as: UTF8.self)
    }

    private enum CodingKeys: String, CodingKey {
        case type, state, players, turnOfPlayerIndices, turnOfPlayerIndex, status, endReason,
             moveCount, myPlayerIndex, actingForPlayerIndex
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(type, forKey: .type)
        try c.encode(state, forKey: .state)
        try c.encode(players, forKey: .players)
        // encode (not encodeIfPresent), so missing values are sent as null
        try c.encode(turnOfPlayerIndices, forKey: .turnOfPlayerIndices)
        try c.encode(turnOfPlayerIndex, forKey: .turnOfPlayerIndex)
        try c.encode(status, forKey: .status)
        try c.encode(endReason, forKey: .endReason)
        try c.encode(moveCount, forKey: .moveCount)
        try c.encode(myPlayerIndex, forKey: .myPlayerIndex)
        try c.encode(actingForPlayerIndex, forKey: .actingForPlayerIndex)
    }
}

/// A `make_move` message from the game, or nil if it's malformed.
struct MakeMove {
    let newState: JSONValue
    let nextTurn: [Int]?

    init?(json: String) {
        guard let value = try? JSONDecoder().decode(JSONValue.self, from: Data(json.utf8)),
              value["type"] == .string("make_move"), let newState = value["new_state"] else { return nil }
        self.newState = newState
        switch value["next_turn_player_indices"] {
        case .null: nextTurn = nil
        case .array(let seats):
            let ints = seats.compactMap(\.int)
            guard ints.count == seats.count else { return nil }
            nextTurn = ints
        default:
            // Older games send a single `next_turn_player_index`; wrap it into a set.
            switch value["next_turn_player_index"] {
            case .null: nextTurn = nil
            case let legacy?: guard let seat = legacy.int else { return nil }; nextTurn = [seat]
            case nil: return nil
            }
        }
    }
}
