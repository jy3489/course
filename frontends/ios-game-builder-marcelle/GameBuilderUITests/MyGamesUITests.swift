import XCTest

/// Create / edit / delete my games, online and offline.
/// Needs the course backend running on the Mac (see README, "Automated tests"):
/// these tests write games, so they never use the live buildplay.fun server.
final class MyGamesUITests: XCTestCase {
    private let server = ["-server", "http://localhost:8000"]
    private var app: XCUIApplication!

    override func setUp() {
        continueAfterFailure = false
    }

    private func launch(_ extra: [String] = []) {
        app = XCUIApplication()
        app.launchArguments = server + ["-guestLogin"] + extra
        app.launch()
    }

    /// Fresh start: forget the local account, then log in as a new guest on the local server.
    private func launchLoggedIn() {
        launch(["-resetLocalData"])
        tap(app.textFields["guest-name"])
        app.textFields["guest-name"].typeText("UI Test")
        tap(app.buttons["play-as-guest"])
        XCTAssertTrue(app.buttons["new-game"].waitForExistence(timeout: 15), "login failed")
    }

    private func tap(_ element: XCUIElement, timeout: TimeInterval = 10) {
        XCTAssertTrue(element.waitForExistence(timeout: timeout), "missing \(element)")
        element.tap()
    }

    private func replaceText(_ field: XCUIElement, with text: String) {
        tap(field)
        let old = (field.value as? String) ?? ""
        field.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: old.count) + text)
    }

    private func createGame(_ name: String, extraCount: Int? = nil) {
        tap(app.buttons["new-game"])
        replaceText(app.textFields["name"], with: name)
        if let extraCount { tap(app.buttons["\(extraCount) players"]) }
        tap(app.buttons["save"])
        XCTAssertTrue(app.staticTexts[name].waitForExistence(timeout: 5), "\(name) isn't listed")
    }

    private var pendingNotice: XCUIElement { app.staticTexts.matching(NSPredicate(format: "label ENDSWITH 'waiting to upload'")).firstMatch }
    private var notUploaded: XCUIElement { app.staticTexts["· not uploaded yet"] }

    private func screenshot(_ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    func testCreateEditPlayDelete() {
        launchLoggedIn()

        // Create: name, and 2 + 3 players.
        tap(app.buttons["new-game"])
        replaceText(app.textFields["name"], with: "Robot Race")
        tap(app.buttons["3 players"])
        screenshot("editor-new-game")
        tap(app.buttons["save"])
        XCTAssertTrue(app.staticTexts["Robot Race"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["2, 3 players"].exists)
        XCTAssertTrue(notUploaded.waitForNonExistence(timeout: 10), "the new game wasn't uploaded")
        screenshot("my-games")

        // Edit: rename; the server makes version 2.
        tap(app.staticTexts["Robot Race"])
        tap(app.buttons["edit-game"])
        replaceText(app.textFields["name"], with: "Robot Race 2")
        tap(app.buttons["save"])
        XCTAssertTrue(app.staticTexts["Robot Race 2"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Version 2"].waitForExistence(timeout: 10), "the edit wasn't uploaded")

        // The starter code plays.
        tap(app.buttons["test-play"])
        tap(app.buttons["start"])
        tap(app.webViews.buttons["+1"], timeout: 15)
        XCTAssertTrue(app.staticTexts["move 2"].waitForExistence(timeout: 10), "the computer didn't move")
        app.navigationBars.buttons.element(boundBy: 0).tap()
        app.navigationBars.buttons.element(boundBy: 0).tap()

        // Delete (with a confirmation).
        tap(app.buttons["edit-game"])
        tap(app.buttons["delete"])
        tap(app.buttons["Delete game"])
        XCTAssertTrue(app.buttons["new-game"].waitForExistence(timeout: 5), "didn't go back to the list")
        XCTAssertTrue(app.staticTexts["Robot Race 2"].waitForNonExistence(timeout: 5))
        XCTAssertTrue(pendingNotice.waitForNonExistence(timeout: 10), "the delete wasn't uploaded")
    }

    func testOfflineEditsAreUploadedLater() {
        launchLoggedIn()
        createGame("Server Game")
        XCTAssertTrue(notUploaded.waitForNonExistence(timeout: 10))
        app.terminate()

        // Airplane mode: create a game, edit it, delete the uploaded one.
        launch(["-simulateOffline"])
        createGame("Offline Game", extraCount: 4)
        XCTAssertTrue(notUploaded.exists)
        tap(app.staticTexts["Offline Game"])
        tap(app.buttons["edit-game"])
        replaceText(app.textFields["name"], with: "Offline Game 2")
        tap(app.buttons["save"])
        XCTAssertTrue(app.staticTexts["Not uploaded yet"].waitForExistence(timeout: 5))
        app.navigationBars.buttons.element(boundBy: 0).tap()

        tap(app.staticTexts["Server Game"])
        tap(app.buttons["edit-game"])
        tap(app.buttons["delete"])
        tap(app.buttons["Delete game"])
        XCTAssertTrue(app.staticTexts["Server Game"].waitForNonExistence(timeout: 5))
        // The create and its edit were merged: 2 changes (create "Offline Game 2", delete "Server Game").
        XCTAssertTrue(app.staticTexts["2 changes waiting to upload"].waitForExistence(timeout: 5))
        screenshot("offline-queue")

        // Still queued after quitting the app.
        app.terminate()
        launch(["-simulateOffline"])
        XCTAssertTrue(app.staticTexts["2 changes waiting to upload"].waitForExistence(timeout: 10))
        app.terminate()

        // Back online: everything is uploaded.
        launch()
        XCTAssertTrue(pendingNotice.waitForNonExistence(timeout: 15), "the queue wasn't uploaded")
        XCTAssertTrue(app.staticTexts["Offline Game 2"].exists)
        XCTAssertFalse(notUploaded.exists)
        XCTAssertFalse(app.staticTexts["Server Game"].exists)
        XCTAssertTrue(app.staticTexts["2, 4 players"].exists)
        screenshot("after-upload")
    }

    // MARK: Unpublished and solo games

    /// A REST call to the local server (to set up what the app should show).
    private func rest(_ method: String, _ path: String, _ body: Any? = nil, as user: [String: Any]? = nil) async throws -> Any {
        var r = URLRequest(url: URL(string: "http://localhost:8000" + path)!)
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

    @MainActor
    func testUnpublishedGame() async throws {
        // A user with one game, unpublished (as the web builder does).
        let me = try await rest("POST", "/users", ["display_name": "UI Test"]) as! [String: Any]
        let game = try await rest("POST", "/games", [
            "name": "Secret Draft", "description": "only I can see it", "allowed_player_counts": [2],
            "allows_leave_mid_match": false, "allows_join_mid_match": false,
            "code": "<!doctype html><title>draft</title>",
        ], as: me) as! [String: Any]
        let id = game["id"] as! String
        _ = try await rest("POST", "/games/\(id)/unpublish", as: me)
        let publicList = try await rest("GET", "/games") as! [[String: Any]]
        XCTAssertFalse(publicList.contains { $0["id"] as? String == id }, "GET /games should hide it")

        launch(["-resetLocalData", "-loginAs", me["id"] as! String, me["password"] as! String])
        // Listed under my games (from GET /builder/games), with the tag.
        let row = app.staticTexts["Secret Draft"]
        XCTAssertTrue(row.waitForExistence(timeout: 15), "my unpublished game isn't listed")
        XCTAssertTrue(app.staticTexts["unpublished-tag"].exists || app.staticTexts["unpublished"].exists)
        screenshot("unpublished-in-my-games")

        // It can't be played online: a clear message instead of "Server error 404".
        row.tap()
        tap(app.buttons["play-online"])
        tap(app.buttons["create-match"])
        let message = app.staticTexts.matching(NSPredicate(format: "label CONTAINS 'isn\u{2019}t published' OR label CONTAINS \"isn't published\"")).firstMatch
        XCTAssertTrue(message.waitForExistence(timeout: 10), "no clear message")
        screenshot("unpublished-play-online")
        app.buttons["cancel"].tap()

        // Editing is done in the web builder; the app says so and doesn't offer saving.
        tap(app.buttons["edit-game"])
        XCTAssertTrue(app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH 'This game is unpublished'")).firstMatch.waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["save"].isEnabled)
        screenshot("unpublished-editor")
    }

    func testSoloGame() {
        launchLoggedIn()
        tap(app.buttons["new-game"])
        replaceText(app.textFields["name"], with: "Solo Race")
        tap(app.buttons["1 player"])            // pick 1 ...
        tap(app.buttons["2 players"])           // ... and drop the default 2
        tap(app.buttons["save"])
        XCTAssertTrue(app.staticTexts["Solo Race"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["1 player (solo)"].exists)

        app.staticTexts["Solo Race"].tap()
        tap(app.buttons["test-play"])
        XCTAssertTrue(app.staticTexts["solo-seat"].waitForExistence(timeout: 5) || app.staticTexts["you (solo)"].exists)
        XCTAssertFalse(app.buttons["seat0-computer"].exists, "a solo game has no computer seat")
        screenshot("solo-setup")
        tap(app.buttons["start"])

        // Every move is mine again: no "pass the phone" screen, no computer.
        let add = app.webViews.buttons["+1"]
        for _ in 0..<3 {
            tap(add, timeout: 15)
            XCTAssertFalse(app.buttons["handoff-ready"].waitForExistence(timeout: 2), "handoff in a solo game")
        }
        XCTAssertTrue(app.staticTexts["turn-indicator"].label.hasPrefix("Your turn"), app.staticTexts["turn-indicator"].label)
        screenshot("solo-play")
    }
}
