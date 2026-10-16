import XCTest

/// Steps that differ between iPhone (one screen at a time, tabs at the bottom) and iPad
/// (list on the left and the screen on the right, tabs at the top).
extension XCUIApplication {
    var isPad: Bool { UIDevice.current.userInterfaceIdiom == .pad }

    /// A tab button, wherever the tab bar is.
    func tab(_ name: String) -> XCUIElement {
        tabBars.buttons[name].exists ? tabBars.buttons[name] : buttons[name].firstMatch
    }

    /// A row in the list by its title. On iPad the same title can also be on the right, as
    /// the open screen's title; the list comes first.
    func row(_ title: String) -> XCUIElement { staticTexts[title].firstMatch }

    /// The scrolling list of the current tab (on iPad, the left column).
    var list: XCUIElement { collectionViews.firstMatch }

    /// A list row (cell) with this title, e.g. to swipe it.
    func listCell(_ title: String) -> XCUIElement {
        list.cells.containing(.staticText, identifier: title).firstMatch
    }

    /// Goes back one screen. On iPad the list stays on the left, so the first screen on the
    /// right has no back button and there's nothing to do.
    func goBack() {
        let back = navigationBars.buttons.matching(identifier: "BackButton").firstMatch
        if back.waitForExistence(timeout: 2) {
            back.tap()
        } else if !isPad {
            navigationBars.buttons.element(boundBy: 0).tap()
        }
    }
}
