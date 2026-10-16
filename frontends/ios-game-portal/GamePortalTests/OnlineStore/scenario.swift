// Uses the production OnlineStore with intercepted HTTP. No class-server writes.
private final class MockServer: URLProtocol {
    static var offline = false
    static var loseAcknowledgement = false
    static var rejectMove = false
    static var writes = 0
    static var snapshot: [String: Any] = ["id": "m", "game_id": "g", "game_version": 7,
        "status": "ongoing", "move_count": 0, "state": NSNull(), "turn_of_player_indices": [0],
        "players": [["player_index": 0, "kind": "human", "user_id": "test"], ["player_index": 1, "kind": "computer"]]]
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        if Self.offline { client?.urlProtocol(self, didFailWithError: URLError(.notConnectedToInternet)); return }
        var body: Any
        var status = 200
        switch request.url!.path {
        case "/games": body = [["id": "g", "name": "Cached Game"]]
        case "/matches": body = [Self.snapshot]
        case "/matches/open": body = [[String: Any]]()
        case "/matches/m": body = Self.snapshot
        case "/games/g/versions/7": body = ["code": "<html>cached version seven</html>"]
        case "/matches/m/moves":
            let data: Data
            if let inline = request.httpBody { data = inline }
            else {
                let stream = request.httpBodyStream!; stream.open(); defer { stream.close() }
                var bytes = Data(); var buffer = [UInt8](repeating: 0, count: 4096)
                while true { let n = stream.read(&buffer, maxLength: buffer.count); if n <= 0 { break }; bytes.append(buffer, count: n) }
                data = bytes
            }
            let move = try! JSONSerialization.jsonObject(with: data) as! [String: Any]
            if Self.rejectMove || move["expected_move_count"] as? Int != Self.snapshot["move_count"] as? Int {
                status = 409; body = ["detail": "stale move"]
            } else {
                Self.writes += 1
                Self.snapshot["state"] = move["new_state"]
                Self.snapshot["turn_of_player_indices"] = move["next_turn_player_indices"]
                Self.snapshot["move_count"] = (Self.snapshot["move_count"] as! Int) + 1
                Self.snapshot["status"] = move["next_turn_player_indices"] is NSNull ? "over" : "ongoing"
                Self.snapshot["end_reason"] = move["next_turn_player_indices"] is NSNull ? "finished" : NSNull()
                if Self.loseAcknowledgement {
                    Self.loseAcknowledgement = false
                    client?.urlProtocol(self, didFailWithError: URLError(.networkConnectionLost)); return
                }
                body = Self.snapshot
            }
        default: fatalError("Unexpected request: \(request)")
        }
        client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)!, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: try! JSONSerialization.data(withJSONObject: body))
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}
@main private struct Scenario {
    @MainActor static func main() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [MockServer.self]
        let session = URLSession(configuration: configuration)
        let user = OnlineIdentity(id: "test", password: "fixture-only", display_name: "Test")
        func store() -> OnlineStore { OnlineStore(identity: user, cacheDirectory: directory, session: session) }
        func message(_ n: Int, _ next: Any) -> [String: Any] {
            ["type": "make_move", "new_state": ["arbitrary": n], "next_turn_player_indices": next]
        }
        let online = store()
        await online.refresh()
        precondition(!online.availableOffline(online.matches[0]), "A snapshot without game code must not appear as playable offline")
        await online.choose(online.matches[0])
        precondition(online.availableOffline(online.matches[0]), "Downloaded code makes the match available offline")
        precondition(online.code.contains("version seven") && online.acting == 0)
        await online.move(message(1, [1]), matchID: "m", count: 0)
        precondition(MockServer.writes == 1 && online.pendingCounts.isEmpty && online.acting == 1)
        MockServer.offline = true
        let offline = store()
        await offline.choose(offline.matches[0])
        precondition(offline.code.contains("version seven") && offline.acting == 1)
        // Game-generated computer reply, followed by human, followed by computer.
        await offline.move(message(2, [0]), matchID: "m", count: 1)
        precondition(offline.acting == 0)
        await offline.move(message(3, [1]), matchID: "m", count: 2)
        await offline.move(message(4, [0]), matchID: "m", count: 3)
        precondition(offline.pendingCounts["m"] == 3 && MockServer.writes == 1)
        await offline.move(message(99, [1]), matchID: "m", count: 2)
        precondition(offline.pendingCounts["m"] == 3, "Reject duplicate/stale bridge message")
        let restarted = store()
        await restarted.choose(restarted.matches[0])
        precondition(restarted.selected?.count == 4 && restarted.pendingCounts["m"] == 3)
        MockServer.offline = false
        try await restarted.refreshSelected()
        precondition(restarted.pendingCounts.isEmpty && MockServer.writes == 4 && restarted.selected?.count == 4)
        // Diverged server state: preserve local branch; do not overwrite the server.
        MockServer.offline = true
        await restarted.move(message(5, [1]), matchID: "m", count: 4)
        MockServer.snapshot["state"] = ["another_client": true]
        MockServer.snapshot["move_count"] = 5
        MockServer.offline = false
        try await restarted.refreshSelected()
        precondition(restarted.conflicts["m"] != nil && restarted.pendingCounts["m"] == 1 && MockServer.writes == 4)
        precondition(restarted.acting == nil)
        let conflictRestart = store()
        await conflictRestart.choose(conflictRestart.matches[0])
        precondition(conflictRestart.conflicts["m"] != nil)
        await conflictRestart.useServerVersion()
        precondition(conflictRestart.conflicts.isEmpty && conflictRestart.pendingCounts.isEmpty)
        // Server accepts a move, but response is lost. No duplicate automatic replay.
        MockServer.loseAcknowledgement = true
        await conflictRestart.move(message(6, [0]), matchID: "m", count: 5)
        precondition(conflictRestart.pendingCounts["m"] == 1)
        await conflictRestart.syncPending()
        precondition(conflictRestart.conflicts["m"] != nil && MockServer.writes == 5)
        await conflictRestart.useServerVersion()
        // Server rejection also preserves the pending branch.
        MockServer.rejectMove = true
        await conflictRestart.move(message(7, [0]), matchID: "m", count: 6)
        precondition(conflictRestart.conflicts["m"] != nil && conflictRestart.pendingCounts["m"] == 1)
        MockServer.rejectMove = false
        await conflictRestart.useServerVersion()
        // Offline end-of-game survives restart and syncs correctly.
        MockServer.offline = true
        await conflictRestart.move(message(7, NSNull()), matchID: "m", count: 6)
        precondition(conflictRestart.selected?.status == "over" && conflictRestart.acting == nil)
        let finished = store()
        await finished.choose(finished.matches[0])
        precondition(finished.selected?.status == "over" && finished.pendingCounts["m"] == 1)
        MockServer.offline = false
        await finished.syncPending()
        precondition(finished.pendingCounts.isEmpty && MockServer.snapshot["status"] as? String == "over")
        // Other humans and spectators cannot take a turn, including while offline.
        MockServer.snapshot["status"] = "ongoing"
        MockServer.snapshot["turn_of_player_indices"] = [1]
        MockServer.snapshot["players"] = [["player_index": 0, "kind": "human", "user_id": "test"], ["player_index": 1, "kind": "human", "user_id": "other"]]
        try await finished.refreshSelected()
        MockServer.offline = true
        let before = MockServer.writes
        await finished.move(message(8, [0]), matchID: "m", count: 7)
        precondition(finished.acting == nil && finished.pendingCounts.isEmpty && MockServer.writes == before)
        MockServer.offline = false
        let spectator = OnlineStore(identity: OnlineIdentity(id: "spectator", password: "fixture", display_name: "Viewer"), cacheDirectory: directory, session: session)
        await spectator.choose(OnlineMatch(json: MockServer.snapshot))
        precondition(spectator.acting == nil)
        print("PASS: online moves, offline human/computer chains, restart, ordered sync, stale messages, conflicts, lost acknowledgement, rejection, explicit resolution and game-over sync")
    }
}
