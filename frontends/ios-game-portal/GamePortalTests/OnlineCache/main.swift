import Foundation

func expect(_ condition: @autoclosure () -> Bool, _ message: String) {
    precondition(condition(), message)
}
let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
defer { try? FileManager.default.removeItem(at: directory) }
let original = try OnlineCache(directory: directory, accountID: "test-user")
expect(original.matches.isEmpty, "Fresh account is empty")
try original.saveGames([["id": "game", "name": "Test game", "code": "do not save unversioned code"]])
let initial: [String: Any] = ["id": "match", "game_id": "game", "game_version": 1,
    "state": NSNull(), "status": "ongoing", "move_count": 0, "players": [["player_index": 0, "user_id": "test-user"]]]
try original.saveMatches([initial])
try original.saveCode("<html>version one</html>", gameID: "game", version: 1)
try original.saveCode("<html>version two</html>", gameID: "game", version: 2)
// A new cache instance represents a process restart, with no network involved.
let restored = try OnlineCache(directory: directory, accountID: "test-user")
expect(restored.matches.count == 1, "Restore list")
expect(restored.matches[0]["state"] is NSNull, "Preserve null initial state")
expect(restored.games[0]["code"] == nil, "Store metadata without unversioned code")
expect(restored.code(gameID: "game", version: 1) == "<html>version one</html>", "Pin old game version")
expect(restored.code(gameID: "game", version: 2) == "<html>version two</html>", "Keep new version separately")
expect(restored.code(gameID: "game", version: 3) == nil, "Never substitute the wrong version")
var changed = initial
changed["state"] = ["arbitrary": ["cards": [1, 2, 3], "score": 5], "finished": false] as [String: Any]
changed["move_count"] = 1
try restored.saveMatch(changed)
let updated = try OnlineCache(directory: directory, accountID: "test-user")
expect(updated.matches.count == 1, "Update rather than duplicate match")
let actual = try JSONSerialization.data(withJSONObject: updated.matches[0], options: .sortedKeys)
let expected = try JSONSerialization.data(withJSONObject: changed, options: .sortedKeys)
expect(actual == expected, "Preserve arbitrary game state exactly")
let other = try OnlineCache(directory: directory, accountID: "different-user")
expect(other.matches.isEmpty && other.code(gameID: "game", version: 1) == nil, "Isolate accounts")
try updated.saveMatches([])
let emptied = try OnlineCache(directory: directory, accountID: "test-user")
expect(emptied.matches.isEmpty, "Persist an empty server list")
// A failed write must not claim that the new state was saved.
let failureDir = directory.appendingPathComponent("failure")
let failureCache = try OnlineCache(directory: failureDir, accountID: "user")
try FileManager.default.removeItem(at: failureDir)
do { try failureCache.saveMatches([initial]); fatalError("Expected write failure") } catch {}
expect(failureCache.matches.isEmpty, "Failed save preserves in-memory cache")
// Malformed/unsupported cache is reported and left untouched.
let files = try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)
let file = files.first!
let corrupt = Data("not JSON".utf8)
try corrupt.write(to: file)
do { _ = try OnlineCache(directory: directory, accountID: "test-user"); fatalError("Expected corrupt cache error") } catch {}
let afterCorruption = try Data(contentsOf: file)
expect(afterCorruption == corrupt, "Do not overwrite corrupt file")
print("PASS: offline restart, null and opaque state, version pinning, metadata, account isolation, empty lists, failed writes and corrupt cache")
