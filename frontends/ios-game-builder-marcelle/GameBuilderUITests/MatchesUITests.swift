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
        tap(app.staticTexts["Tic-tac-toe"])
        tap(app.buttons["play-online"])
        tap(app.buttons["computers-\(computers)"])
        tap(app.buttons["create-match"])
        let link = app.staticTexts["match-link"]
        XCTAssertTrue(link.waitForExistence(timeout: 10))
        return link.label.components(separatedBy: "match=").last!
    }

    private var cells: XCUIElementQuery { app.webViews.buttons }

    private func openMatchesTab() { tap(app.tabBars.buttons["Matches"]) }

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

    /// Friend puts an O in `cell`, then the turn goes to `next`.
    private func friendMoves(_ id: String, cell: Int, next: [Int], as friend: [String: Any]) async throws {
        let m = try await match(id)
        var board = (m["state"] as? [String: Any])?["board"] as? [String] ?? Array(repeating: "", count: 9)
        board[cell] = "O"
        _ = try await request("POST", "/matches/\(id)/moves", [
            "new_state": ["board": board, "winner": NSNull(), "draw": false],
            "next_turn_player_indices": next,
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
        app.navigationBars.buttons.element(boundBy: 0).tap()
        app.navigationBars.buttons.element(boundBy: 0).tap()
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
        app.navigationBars.buttons.element(boundBy: 0).tap()
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
        app.navigationBars.buttons.element(boundBy: 0).tap()
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
}
