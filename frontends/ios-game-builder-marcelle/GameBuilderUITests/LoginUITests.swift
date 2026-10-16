import XCTest

/// Google login can't finish in a test (it needs a real Google account), but tapping the
/// button must open Google's sign-in page (after iOS asks "wants to use google.com to sign
/// in"). Needs the internet.
@MainActor
final class LoginUITests: XCTestCase {
    func testGoogleButtonOpensGoogleSignIn() {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["-resetLocalData"]
        app.launch()
        XCTAssertFalse(app.textFields["guest-name"].exists, "guest login should be hidden")
        app.buttons["google-sign-in"].tap()

        let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        let prompt = springboard.staticTexts.matching(NSPredicate(format: "label CONTAINS 'google.com'")).firstMatch
        XCTAssertTrue(prompt.waitForExistence(timeout: 10), "Google's sign-in prompt didn't appear")
        let attachment = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        attachment.name = "google-prompt"
        attachment.lifetime = .keepAlways
        add(attachment)
        springboard.buttons["Continue"].tap()

        // Google's page loads. A wrong client ID or URL scheme shows an error page instead.
        let page = app.webViews.firstMatch
        XCTAssertTrue(page.staticTexts.matching(NSPredicate(format: "label CONTAINS[c] 'sign in'")).firstMatch
            .waitForExistence(timeout: 20), "Google's sign-in page didn't load")
        let googleError = page.staticTexts.matching(NSPredicate(format: "label CONTAINS[c] 'error' OR label CONTAINS 'invalid_'")).firstMatch
        let shot = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        shot.name = "google-page"
        shot.lifetime = .keepAlways
        add(shot)
        XCTAssertFalse(googleError.exists, "Google showed an error: \(googleError.label)")
    }
}
