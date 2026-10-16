package com.dapillah.gameportal

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.*
import java.net.URI
import java.net.URLDecoder

val PortalJson = Json { ignoreUnknownKeys = true; encodeDefaults = true; explicitNulls = true }

@Serializable data class User(val id: String, @SerialName("display_name") val name: String, val password: String? = null)
@Serializable data class Credentials(val id: String, val name: String, val password: String)
@Serializable data class NewUser(@SerialName("display_name") val name: String)
@Serializable data class Game(
    val id: String, val name: String, val description: String = "", val version: Int,
    @SerialName("allowed_player_counts") val counts: List<Int>,
    @SerialName("allows_leave_mid_match") val allowsLeave: Boolean = false,
    @SerialName("allows_join_mid_match") val allowsJoin: Boolean = false,
    val code: String, val deleted: Boolean = false,
)
@Serializable data class Player(
    @SerialName("player_index") val index: Int, val kind: String,
    @SerialName("user_id") val userId: String? = null,
)
@Serializable data class Match(
    val id: String, @SerialName("game_id") val gameId: String,
    @SerialName("game_version") val gameVersion: Int,
    @SerialName("owner_user_id") val ownerId: String,
    val status: String, val players: List<Player>,
    @SerialName("turn_of_player_indices") val turn: List<Int>? = null,
    val state: JsonElement = JsonNull,
    @SerialName("move_count") val moveCount: Int,
    @SerialName("end_reason") val endReason: String? = null,
) {
    val key get() = "$id@$gameId@$gameVersion"
    fun seat(userId: String?) = players.firstOrNull { it.userId != null && it.userId == userId }
    fun actingFor(userId: String?): Int? {
        if (status != "ongoing" || turn == null) return null
        val mine = seat(userId) ?: return null
        if (mine.index in turn) return mine.index
        return players.firstOrNull { it.kind == "computer" && it.index in turn }?.index
    }
}
@Serializable data class NewMatch(@SerialName("game_id") val gameId: String, @SerialName("num_computer_opponents") val computers: Int = 0)
@Serializable data class StartMatch(@SerialName("first_turn_player_indices") val turn: List<Int> = listOf(0), @SerialName("initial_state") val state: JsonElement = JsonNull)
@Serializable data class Computers(@SerialName("num_computer_opponents") val number: Int)
@Serializable data class Move(
    @SerialName("new_state") val state: JsonElement,
    @SerialName("next_turn_player_indices") val next: List<Int>?,
    @SerialName("expected_move_count") val expected: Int,
)

@Serializable data class AgentMessage(val role: String, val content: String)
@Serializable data class AgentChatRequest(
    @SerialName("game_name") val gameName: String,
    @SerialName("game_description") val gameDescription: String,
    @SerialName("allowed_player_counts") val allowedPlayerCounts: List<Int>,
    val code: String,
    val messages: List<AgentMessage>
)
@Serializable data class AgentChatResponse(
    val message: String,
    val code: String? = null,
    val name: String? = null,
    val description: String? = null
)
@Serializable data class NewGame(
    val name: String,
    val description: String = "",
    @SerialName("allowed_player_counts") val counts: List<Int>,
    @SerialName("allows_leave_mid_match") val allowsLeave: Boolean = false,
    @SerialName("allows_join_mid_match") val allowsJoin: Boolean = false,
    val code: String
)

object GameProtocol {
    fun stateChanged(match: Match, userId: String?, names: Map<String, String>, canMove: Boolean): JsonObject = buildJsonObject {
        put("type", "state_changed")
        put("state", match.state)
        putJsonArray("players") {
            match.players.forEach { p -> add(buildJsonObject {
                put("player_index", p.index); put("kind", p.kind)
                put("name", if (p.kind == "computer") "Computer ${p.index + 1}" else names[p.userId] ?: p.userId?.take(8) ?: "Player")
            }) }
        }
        put("turn_of_player_indices", match.turn?.let { JsonArray(it.map(::JsonPrimitive)) } ?: JsonNull)
        put("turn_of_player_index", match.turn?.firstOrNull()?.let(::JsonPrimitive) ?: JsonNull)
        put("status", match.status)
        put("end_reason", match.endReason?.let(::JsonPrimitive) ?: JsonNull)
        put("move_count", match.moveCount)
        put("my_player_index", match.seat(userId)?.index?.let(::JsonPrimitive) ?: JsonNull)
        put("acting_for_player_index", (if (canMove) match.actingFor(userId) else null)?.let(::JsonPrimitive) ?: JsonNull)
    }

    fun parseMove(message: JsonObject, expected: Int): Move {
        require(message["type"]?.jsonPrimitive?.content == "make_move") { "Unexpected game message" }
        require(message.containsKey("new_state")) { "The game did not supply a state" }
        val value = if (message.containsKey("next_turn_player_indices")) message.getValue("next_turn_player_indices")
            else message["next_turn_player_index"]?.let { if (it == JsonNull) JsonNull else JsonArray(listOf(it)) }
                ?: error("The game did not supply the next turn")
        val next = if (value == JsonNull) null else {
            require(value is JsonArray && value.isNotEmpty()) { "Invalid next turn" }
            value.map {
                require(it is JsonPrimitive && !it.isString && it.intOrNull != null) { "Invalid player index" }
                it.int.also { index -> require(index >= 0) { "Invalid player index" } }
            }.also { require(it.distinct().size == it.size) { "Duplicate player index" } }
        }
        return Move(message.getValue("new_state"), next, expected)
    }
}

fun matchIdFromInput(input: String, baseUrl: String): String {
    val text = input.trim()
    val id = if (text.contains("://")) {
        val uri = URI(text)
        val base = URI(baseUrl)
        require(uri.scheme == base.scheme && uri.host == base.host && uri.port == base.port) {
            "This link belongs to a different game server"
        }
        val matchPart = uri.rawFragment?.split('&')?.firstOrNull { it.startsWith("match=") }
            ?: error("This link has no match ID")
        URLDecoder.decode(matchPart.removePrefix("match="), "UTF-8")
    } else text
    require(id.matches(Regex("[A-Za-z0-9_-]{1,128}"))) { "Paste a match ID or a shared match link" }
    return id
}
