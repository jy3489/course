import XCTest

/// Taps through test play on the simulator with the real games from buildplay.fun.
/// Uses the debug launch arguments `-screenshotMode` (placeholder account, no login),
/// `-openGame <id>` and `-simulateOffline` (every server request fails).
final class PlayUITests: XCTestCase {
    private var app: XCUIApplication!

    override func setUp() {
        continueAfterFailure = false
    }

    private func launch(game: String, offline: Bool = false) {
        app = XCUIApplication()
        app.launchArguments = ["-screenshotMode", "-openGame", game] + (offline ? ["-simulateOffline"] : [])
        app.launch()
    }

    private func startTestPlay(players: Int? = nil, humanSeats: [Int] = []) {
        tap(app.buttons["test-play"])
        if let players { tap(app.buttons["\(players) players"]) }
        for seat in humanSeats { tap(app.buttons["seat\(seat)-human"]) }
        tap(app.buttons["start"])
    }

    private func tap(_ element: XCUIElement, timeout: TimeInterval = 15) {
        XCTAssertTrue(element.waitForExistence(timeout: timeout), "missing \(element)")
        element.tap()
    }

    private var status: String { app.staticTexts["turn-indicator"].label }

    private func waitForStatus(_ prefix: String, timeout: TimeInterval = 15) {
        let predicate = NSPredicate(format: "label BEGINSWITH %@", prefix)
        let found = app.staticTexts["turn-indicator"].waitFor(predicate, timeout: timeout)
        XCTAssertTrue(found, "status was \"\(status)\", expected \"\(prefix)…\"")
    }

    /// The 9 tic-tac-toe cells: buttons inside the game's web page.
    private var cells: XCUIElementQuery { app.webViews.buttons }

    private func emptyCell() -> XCUIElement {
        cells.matching(NSPredicate(format: "label == '' AND enabled == true")).firstMatch
    }

    private func screenshot(_ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    func testTicTacToeVsComputer() {
        launch(game: "tictactoe")
        startTestPlay()
        waitForStatus("Your turn")
        XCTAssertTrue(cells.element(boundBy: 8).waitForExistence(timeout: 15))
        XCTAssertEqual(cells.count, 9)

        // My move, then the game plays the computer's move by itself.
        cells.element(boundBy: 4).tap()
        XCTAssertTrue(cells["O"].waitForExistence(timeout: 10), "the computer didn't move")
        waitForStatus("Your turn")
        XCTAssertTrue(cells["X"].exists)
        screenshot("tictactoe-vs-computer")

        // Undo takes back the computer's move and mine.
        tap(app.buttons["undo"])
        XCTAssertTrue(cells["X"].waitForNonExistence(timeout: 5))
        XCTAssertFalse(cells["O"].exists)

        // Play to the end (the computer plays perfectly, so it's a draw or a loss).
        for _ in 0..<5 where !status.hasPrefix("Game over") {
            waitForStatus("Your turn")
            emptyCell().tap()
            let predicate = NSPredicate(format: "NOT (label BEGINSWITH 'Computer')")
            _ = app.staticTexts["turn-indicator"].waitFor(predicate, timeout: 10)
        }
        waitForStatus("Game over")
        screenshot("tictactoe-game-over")

        tap(app.buttons["restart"])
        waitForStatus("Your turn")
        XCTAssertTrue(cells["X"].waitForNonExistence(timeout: 5))
    }

    func testTicTacToePassAndPlay() {
        launch(game: "tictactoe")
        startTestPlay(humanSeats: [1])
        waitForStatus("Your turn, Player 1")
        XCTAssertTrue(cells.element(boundBy: 8).waitForExistence(timeout: 15))
        cells.element(boundBy: 0).tap()

        // The phone is passed to Player 2 before their turn is shown.
        XCTAssertTrue(app.buttons["handoff-ready"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Pass the phone to Player 2"].exists)
        screenshot("pass-and-play-handoff")
        app.buttons["handoff-ready"].tap()
        waitForStatus("Your turn, Player 2")
        emptyCell().tap()

        XCTAssertTrue(app.staticTexts["Pass the phone to Player 1"].waitForExistence(timeout: 5))
        app.buttons["handoff-ready"].tap()
        waitForStatus("Your turn, Player 1")
        XCTAssertTrue(cells["X"].exists && cells["O"].exists)
        screenshot("pass-and-play-after-handoff")
    }

    func testPokerThreePlayers() {
        launch(game: "poker")
        startTestPlay(players: 3)  // seat 1 human, seats 2 and 3 computers

        // The first move deals; then the human gets betting buttons when it's their turn.
        let action = app.webViews.buttons.matching(NSPredicate(format: "label == 'Fold' OR label BEGINSWITH 'Call' OR label == 'Check'"))
        XCTAssertTrue(action.firstMatch.waitForExistence(timeout: 20), "no betting buttons appeared")
        waitForStatus("Your turn")
        screenshot("poker-your-turn")
        let movesBefore = app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH 'move '")).firstMatch.label

        let callOrCheck = app.webViews.buttons.matching(NSPredicate(format: "label BEGINSWITH 'Call' OR label == 'Check'")).firstMatch
        callOrCheck.tap()
        // The computers answer; eventually it's my turn again (or a new hand starts).
        let moved = app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH 'move ' AND label != %@", movesBefore)).firstMatch
        XCTAssertTrue(moved.waitForExistence(timeout: 10))
        waitForStatus("Your turn", timeout: 20)
        screenshot("poker-after-call")

        tap(app.buttons["undo"])
        waitForStatus("Your turn")
    }

    func testOfflinePlay() {
        // Online first, so the games (and their code) are saved on the phone.
        launch(game: "tictactoe")
        XCTAssertTrue(app.buttons["test-play"].waitForExistence(timeout: 15))
        app.terminate()

        launch(game: "tictactoe", offline: true)
        startTestPlay()
        waitForStatus("Your turn")
        XCTAssertTrue(cells.element(boundBy: 8).waitForExistence(timeout: 15))
        cells.element(boundBy: 4).tap()
        XCTAssertTrue(cells["O"].waitForExistence(timeout: 10), "offline: the computer didn't move")
        screenshot("offline-play")

        // The games list shows the saved games with the offline notice.
        app.terminate()
        app.launchArguments = ["-screenshotMode", "-simulateOffline"]
        app.launch()
        XCTAssertTrue(app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH 'Offline'")).firstMatch.waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["Tic-tac-toe"].exists)
        screenshot("offline-games-list")
    }
}

private extension XCUIElement {
    func waitFor(_ predicate: NSPredicate, timeout: TimeInterval) -> Bool {
        let expectation = XCTNSPredicateExpectation(predicate: predicate, object: self)
        return XCTWaiter.wait(for: [expectation], timeout: timeout) == .completed
    }
}
