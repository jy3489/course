import XCTest

/// Online matches against the course backend running on the Mac (see README, "Automated
/// tests"). The test itself plays a second person ("Friend") through the REST API.
@MainActor
final class MatchesUITests: XCTestCase {
    private let serverURL = "http://localhost:8000"
    private var app: XCUIApplication!

    override func setUp() {
        continueAfterFailure = false
    }

    // MARK: App helpers

    private func launch(_ extra: [String] = []) {
        app = XCUIApplication()
        app.launchArguments = ["-server", serverURL, "-guestLogin"] + extra
        app.launch()
    }

    private func launchLoggedIn() {
        launch(["-resetLocalData"])
        tap(app.textFields["guest-name"])
        app.textFields["guest-name"].typeText("Marcelle")
        tap(app.buttons["play-as-guest"])
        XCTAssertTrue(app.buttons["new-game"].waitForExistence(timeout: 15), "login failed")
    }

    private func tap(_ element: XCUIElement, timeout: TimeInterval = 10) {
        XCTAssertTrue(element.waitForExistence(timeout: timeout), "missing \(element)")
        element.tap()
    }

    /// Games tab → Tic-tac-toe → play online → create; returns the match id.
    private func createTicTacToeMatch(computers: Int) -> String {
        tap(app.row("Tic-tac-toe"))
        tap(app.buttons["play-online"])
        tap(app.buttons["computers-\(computers)"])
        tap(app.buttons["create-match"])
        let link = app.staticTexts["match-link"]
        XCTAssertTrue(link.waitForExistence(timeout: 10))
        return link.label.components(separatedBy: "match=").last!
    }

    private var cells: XCUIElementQuery { app.webViews.buttons }

    private func openMatchesTab() { tap(app.tab("Matches")) }

    /// From a match screen back to the Games list.
    private func backToGames() {
        app.goBack()
        app.goBack()
    }

    /// My match is the first "Tic-tac-toe" (open matches from other players come after it).
    private func openFirstMatch() {
        tap(app.staticTexts["Tic-tac-toe"].firstMatch)
        XCTAssertTrue(cells.element(boundBy: 8).waitForExistence(timeout: 15), "the board didn't load")
    }

    private func screenshot(_ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    // MARK: REST helpers (the second player)

    private func request(_ method: String, _ path: String, _ body: Any? = nil, as user: [String: Any]? = nil) async throws -> Any {
        var r = URLRequest(url: URL(string: serverURL + path)!)
        r.httpMethod = method
        if let body {
            r.httpBody = try JSONSerialization.data(withJSONObject: body)
            r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        if let user {
            r.setValue(user["id"] as? String, forHTTPHeaderField: "X-User-Id")
            r.setValue(user["password"] as? String, forHTTPHeaderField: "X-User-Password")
        }
        let (data, response) = try await URLSession.shared.data(for: r)
        let status = (response as! HTTPURLResponse).statusCode
        XCTAssertTrue((200..<300).contains(status), "\(method) \(path): \(status) \(String(decoding: data, as: UTF8.self))")
        return data.isEmpty ? [:] : try JSONSerialization.jsonObject(with: data)
    }

    /// The HTTP status of a request (for checking that something is gone).
    private func statusCode(_ method: String, _ path: String) async throws -> Int {
        var r = URLRequest(url: URL(string: serverURL + path)!)
        r.httpMethod = method
        let (_, response) = try await URLSession.shared.data(for: r)
        return (response as! HTTPURLResponse).statusCode
    }

    /// Friend creates a match (no computers); the app opens it by link and joins.
    private func joinFriendsMatch(game: String) async throws -> (String, [String: Any]) {
        let friend = try await newFriend()
        let m = try await request("POST", "/matches", ["game_id": game, "num_computer_opponents": 0], as: friend) as! [String: Any]
        let id = m["id"] as! String
        openMatchesTab()
        // The list only builds rows on screen; "join by link" is below the open matches.
        let field = app.textFields["match-link-field"]
        for _ in 0..<15 where !field.exists { app.list.swipeUp() }
        sleep(1) // let the scrolling stop before tapping
        tap(field)
        if !((field.value(forKey: "hasKeyboardFocus") as? Bool) ?? false) { sleep(1); field.tap() }
        field.typeText(id)
        tap(app.buttons["open"])
        tap(app.buttons["join-match"])
        XCTAssertTrue(app.staticTexts["Friend"].waitForExistence(timeout: 10))
        return (id, friend)
    }

    /// Scrolls the Matches list back up to "// my matches" (rows off screen aren't built).
    private func scrollToMyMatches() {
        let label = app.staticTexts["// my matches"]
        for _ in 0..<15 where !(label.exists && label.isHittable) { app.list.swipeDown() }
    }

    private func newFriend() async throws -> [String: Any] {
        try await request("POST", "/users", ["display_name": "Friend"]) as! [String: Any]
    }

    private func match(_ id: String) async throws -> [String: Any] {
        try await request("GET", "/matches/\(id)") as! [String: Any]
    }

    private func waitForMoveCount(_ id: String, _ count: Int, timeout: TimeInterval = 15) async throws {
        let end = Date().addingTimeInterval(timeout)
        while Date() < end {
            if try await match(id)["move_count"] as? Int == count { return }
            try await Task.sleep(for: .milliseconds(500))
        }
        XCTFail("match \(id) never reached \(count) moves")
    }

    /// Friend puts an O in `cell`, then the turn goes to `next` (nil ends the match).
    private func friendMoves(_ id: String, cell: Int, next: [Int]?, as friend: [String: Any]) async throws {
        let m = try await match(id)
        var board = (m["state"] as? [String: Any])?["board"] as? [String] ?? Array(repeating: "", count: 9)
        board[cell] = "O"
        _ = try await request("POST", "/matches/\(id)/moves", [
            "new_state": ["board": board, "winner": NSNull(), "draw": false],
            "next_turn_player_indices": next.map { $0 as Any } ?? NSNull(),
            "expected_move_count": m["move_count"] as! Int,
        ], as: friend)
    }

    /// Creates a Tic-tac-toe match with no computers, Friend joins, the app starts it.
    private func startMatchWithFriend() async throws -> (String, [String: Any]) {
        let id = createTicTacToeMatch(computers: 0)
        let friend = try await newFriend()
        _ = try await request("POST", "/matches/\(id)/join", as: friend)
        XCTAssertTrue(app.staticTexts["Friend"].waitForExistence(timeout: 10), "the app didn't see Friend join")
        screenshot("waiting-room")
        tap(app.buttons["start-match"])
        XCTAssertTrue(cells.element(boundBy: 8).waitForExistence(timeout: 15))
        return (id, friend)
    }

    // MARK: Tests


    func testMatchVsComputer() async throws {
        launchLoggedIn()
        let id = createTicTacToeMatch(computers: 1)
        XCTAssertTrue(app.images["QR code for the match link"].exists)
        tap(app.buttons["start-match"])
        XCTAssertTrue(cells.element(boundBy: 8).waitForExistence(timeout: 15))
        cells.element(boundBy: 4).tap()
        // My app also plays the computer's move and sends it.
        XCTAssertTrue(cells["O"].waitForExistence(timeout: 10), "the computer didn't move")
        try await waitForMoveCount(id, 2)
        screenshot("online-vs-computer")
    }


    func testTwoPlayersAndOfflineMove() async throws {
        launchLoggedIn()
        let (id, friend) = try await startMatchWithFriend()

        // I move; Friend answers through the server; my app sees it by polling.
        cells.element(boundBy: 0).tap()
        try await waitForMoveCount(id, 1)
        try await friendMoves(id, cell: 4, next: [0], as: friend)
        XCTAssertTrue(cells["O"].waitForExistence(timeout: 8), "the app didn't show Friend's move")
        screenshot("online-two-players")
        app.goBack()
        app.goBack()
        openMatchesTab()
        XCTAssertTrue(app.staticTexts["your turn"].waitForExistence(timeout: 10))
        screenshot("my-matches")
        app.terminate()

        // Airplane mode: the saved match opens and I can move; the move waits.
        launch(["-simulateOffline"])
        openMatchesTab()
        openFirstMatch()
        cells.matching(NSPredicate(format: "label == ''")).element(boundBy: 0).tap()
        XCTAssertTrue(app.otherElements["move-pending"].waitForExistence(timeout: 5)
                      || app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH 'Offline: your move'")).firstMatch.exists)
        screenshot("offline-move")
        app.goBack()
        XCTAssertTrue(app.staticTexts["1 move waiting to upload"].waitForExistence(timeout: 5))
        app.terminate()

        // Back online: the move is sent.
        launch()
        openMatchesTab()
        XCTAssertTrue(app.staticTexts["1 move waiting to upload"].waitForNonExistence(timeout: 15))
        try await waitForMoveCount(id, 3)
    }


    func testStaleOfflineMoveIsRejected() async throws {
        launchLoggedIn()
        let (id, friend) = try await startMatchWithFriend()
        cells.element(boundBy: 0).tap()
        try await waitForMoveCount(id, 1)
        // Friend moves and gives the next turn to both of us: whoever moves first wins.
        try await friendMoves(id, cell: 4, next: [0, 1], as: friend)
        XCTAssertTrue(cells["O"].waitForExistence(timeout: 8))
        app.terminate()

        // Offline I move (queued with expected_move_count 2)...
        launch(["-simulateOffline"])
        openMatchesTab()
        openFirstMatch()
        cells.matching(NSPredicate(format: "label == ''")).element(boundBy: 0).tap()
        app.goBack()
        XCTAssertTrue(app.staticTexts["1 move waiting to upload"].waitForExistence(timeout: 5))
        app.terminate()

        // ...but Friend moves first on the server.
        try await friendMoves(id, cell: 8, next: [0], as: friend)

        // Back online: my move is rejected as stale, I'm told, and the match reloads.
        launch()
        openMatchesTab()
        let notice = app.staticTexts.matching(NSPredicate(format: "label CONTAINS 'too late'")).firstMatch
        XCTAssertTrue(notice.waitForExistence(timeout: 15), "no stale-move message")
        XCTAssertTrue(app.staticTexts["move 3"].waitForExistence(timeout: 5), "the match wasn't reloaded")
        screenshot("stale-move-rejected")
        openFirstMatch()
        XCTAssertEqual(cells.matching(NSPredicate(format: "label == 'X'")).count, 1)
        XCTAssertEqual(cells.matching(NSPredicate(format: "label == 'O'")).count, 2)
    }

    func testStatusChips() async throws {
        launchLoggedIn()
        // over: I move, Friend moves and ends the match.
        let (over, friend) = try await startMatchWithFriend()
        cells.element(boundBy: 0).tap()
        try await waitForMoveCount(over, 1)
        try await friendMoves(over, cell: 4, next: nil, as: friend)
        backToGames()
        // their turn: I move.
        let (theirs, _) = try await startMatchWithFriend()
        cells.element(boundBy: 0).tap()
        try await waitForMoveCount(theirs, 1)
        backToGames()
        // your turn: just started, I go first.
        _ = try await startMatchWithFriend()
        backToGames()
        // waiting for players: created, not started.
        _ = createTicTacToeMatch(computers: 0)
        backToGames()

        // ongoing: Friend plays Poker (joinable mid-match) with a computer; I'm not in it.
        let friend2 = try await newFriend()
        let poker = try await request("POST", "/matches", ["game_id": "poker", "num_computer_opponents": 1], as: friend2) as! [String: Any]
        _ = try await request("POST", "/matches/\(poker["id"] as! String)/start", [:], as: friend2)

        openMatchesTab()
        for chip in ["waiting for players", "your turn", "their turn", "over", "ongoing"] {
            XCTAssertTrue(app.staticTexts[chip].waitForExistence(timeout: 10), "no \(chip) chip")
        }
        screenshot("status-chips")
        let ongoing = app.staticTexts["ongoing"]
        for _ in 0..<10 where !ongoing.isHittable { app.list.swipeUp() }
        screenshot("status-chip-ongoing")
    }

    // MARK: Delete, hide, leave

    func testDeleteOwnMatch() async throws {
        launchLoggedIn()
        let id = createTicTacToeMatch(computers: 1)
        backToGames()
        openMatchesTab()
        let row = app.staticTexts["Tic-tac-toe"].firstMatch
        XCTAssertTrue(row.waitForExistence(timeout: 10))
        app.listCell("Tic-tac-toe").swipeLeft()
        tap(app.buttons["delete"])
        XCTAssertTrue(app.alerts["Delete this match for everyone?"].waitForExistence(timeout: 5))
        screenshot("delete-confirm")
        app.alerts.buttons["Delete"].tap()
        let empty = app.staticTexts["Create a match from a game's page (\"play online\"), or join one below."]
        XCTAssertTrue(empty.waitForExistence(timeout: 10), "the match is still listed")
        let status = try await statusCode("GET", "/matches/\(id)")
        XCTAssertEqual(status, 404, "the server still has the match")
        // Not brought back by a refresh or a restart (the saved copy is gone too).
        app.terminate()
        launch()
        openMatchesTab()
        XCTAssertTrue(empty.waitForExistence(timeout: 10), "the deleted match came back")
    }

    func testHideFinishedMatch() async throws {
        launchLoggedIn()
        let (id, friend) = try await joinFriendsMatch(game: "tictactoe")
        _ = try await request("POST", "/matches/\(id)/start", [:], as: friend)
        // Friend's first move ends the match (next turn: null).
        _ = try await request("POST", "/matches/\(id)/moves", [
            "new_state": ["board": ["O", "", "", "", "", "", "", "", ""], "winner": NSNull(), "draw": false],
            "next_turn_player_indices": NSNull(), "expected_move_count": 0,
        ], as: friend)
        app.goBack()
        scrollToMyMatches()
        app.list.swipeDown() // pull to refresh
        XCTAssertTrue(app.staticTexts["over"].waitForExistence(timeout: 10))
        app.listCell("Tic-tac-toe").swipeLeft()
        tap(app.buttons["hide"])               // no confirmation for hiding
        XCTAssertTrue(app.staticTexts["over"].waitForNonExistence(timeout: 10))
        // Hidden only for me: the match itself still exists.
        let match = try await match(id)
        XCTAssertEqual(match["status"] as? String, "over")
        app.terminate()
        launch()
        openMatchesTab()
        XCTAssertFalse(app.staticTexts["over"].waitForExistence(timeout: 5), "the hidden match came back")
    }

    func testLeaveOngoingMatch() async throws {
        launchLoggedIn()
        let (id, friend) = try await joinFriendsMatch(game: "poker")
        _ = try await request("POST", "/matches/\(id)/start", [:], as: friend)
        XCTAssertTrue(app.webViews.firstMatch.waitForExistence(timeout: 15), "the game didn't load")
        tap(app.buttons["match-menu"])
        tap(app.buttons["leave"])
        let alert = app.alerts["Leave this match?"]
        XCTAssertTrue(alert.waitForExistence(timeout: 5))
        XCTAssertTrue(alert.staticTexts["A computer takes over your seat and keeps playing."].exists)
        screenshot("leave-confirm")
        alert.buttons["Leave"].tap()
        // Back on the list, without my seat in it (it may still be listed under open matches,
        // since Poker lets people join mid-match). On the server a computer has my seat.
        XCTAssertTrue(app.tab("Matches").waitForExistence(timeout: 10))
        scrollToMyMatches()
        XCTAssertTrue(app.staticTexts["Friend · Marcelle"].waitForNonExistence(timeout: 10), "still listed as my match")
        let m = try await match(id)
        XCTAssertEqual(m["status"] as? String, "ongoing")
        let seats = m["players"] as! [[String: Any]]
        XCTAssertEqual(seats[1]["kind"] as? String, "computer", "nobody took over my seat")
        screenshot("after-leave")
    }

    func testActionsNeedInternet() async throws {
        launchLoggedIn()
        _ = createTicTacToeMatch(computers: 1)
        app.terminate()
        launch(["-simulateOffline"])
        openMatchesTab()
        let row = app.staticTexts["Tic-tac-toe"].firstMatch
        XCTAssertTrue(row.waitForExistence(timeout: 10))
        app.listCell("Tic-tac-toe").swipeLeft()
        let note = app.buttons["needs internet"]
        XCTAssertTrue(note.waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["delete"].exists)
        screenshot("needs-internet")
        note.tap()                              // does nothing: no confirmation, match stays
        XCTAssertFalse(app.alerts.firstMatch.waitForExistence(timeout: 2))
        XCTAssertTrue(row.exists)
    }
}
