package com.dapillah.gameportal

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.webkit.WebViewClient
import android.annotation.SuppressLint
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel

private val Ink = Color(0xFF111827)
private val Violet = Color(0xFFC2ACFF)
private val Mint = Color(0xFF86E8C8)
private val Muted = Color(0xFFA9B1C4)
private val PortalColors = darkColorScheme(primary = Violet, onPrimary = Color(0xFF271A46),
    secondary = Mint, background = Ink, surface = Color(0xFF1B2435),
    surfaceVariant = Color(0xFF273247), onSurface = Color(0xFFF3F5FA), onBackground = Color(0xFFF3F5FA))

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge(statusBarStyle = SystemBarStyle.dark(android.graphics.Color.TRANSPARENT),
            navigationBarStyle = SystemBarStyle.dark(android.graphics.Color.TRANSPARENT))
        setContent { MaterialTheme(colorScheme = PortalColors) { PortalApp() } }
    }
}

@Composable
fun PortalApp(vm: PortalViewModel = viewModel()) {
    val state by vm.state.collectAsStateWithLifecycle()
    val owner = LocalLifecycleOwner.current
    DisposableEffect(owner) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_START) vm.setActive(true)
            if (event == Lifecycle.Event.ON_STOP) vm.setActive(false)
        }
        owner.lifecycle.addObserver(observer)
        if (owner.lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED)) vm.setActive(true)
        onDispose { owner.lifecycle.removeObserver(observer); vm.setActive(false) }
    }
    BackHandler(enabled = state.selectedId != null) { vm.closeMatch() }
    Surface(Modifier.fillMaxSize(), color = Ink) {
        Column(Modifier.fillMaxSize().safeDrawingPadding().imePadding()) {
            if (state.account == null) {
                Welcome(state, vm::signIn, vm::retry)
            } else {
                Header(state, vm::closeMatch, vm::retry)
                state.error?.let { ErrorBanner(it, vm::retry) }
                if (state.selectedId != null) {
                    if (state.match == null || state.game == null) {
                        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { CircularProgressIndicator() }
                    } else MatchScreen(state, vm)
                } else Lobby(state, vm)
            }
        }
    }
}

@Composable private fun Welcome(state: PortalState, signIn: (String) -> Unit, retry: () -> Unit) {
    var name by rememberSaveable { mutableStateOf("") }
    val keyboard = LocalSoftwareKeyboardController.current
    Column(Modifier.fillMaxSize().padding(28.dp), verticalArrangement = Arrangement.Center) {
        Text("✕  ○", color = Violet, fontSize = 62.sp, fontWeight = FontWeight.Bold)
        Spacer(Modifier.height(28.dp))
        Text("A little game.\nA good connection.", fontSize = 36.sp, lineHeight = 42.sp, fontWeight = FontWeight.Bold)
        Spacer(Modifier.height(14.dp))
        Text("Welcome to Playroom. Pick a name, find a friend, and make your move.", color = Muted, style = MaterialTheme.typography.bodyLarge)
        Spacer(Modifier.height(32.dp))
        OutlinedTextField(name, { if (it.length <= 200) name = it }, label = { Text("Your player name") },
            singleLine = true, modifier = Modifier.fillMaxWidth(), keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Words),
            shape = RoundedCornerShape(16.dp))
        Spacer(Modifier.height(16.dp))
        Button({ keyboard?.hide(); signIn(name) }, enabled = !state.busy && name.isNotBlank(),
            modifier = Modifier.fillMaxWidth().height(56.dp), shape = RoundedCornerShape(16.dp)) {
            if (state.busy) CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
            else Text("Let's play", fontWeight = FontWeight.Bold)
        }
        Spacer(Modifier.height(16.dp))
        Text("Your guest account stays on this device. Your matches are saved on the game server.", color = Muted, style = MaterialTheme.typography.bodySmall)
        state.error?.let { Spacer(Modifier.height(12.dp)); ErrorBanner(it, retry) }
    }
}

@Composable private fun Header(state: PortalState, back: () -> Unit, retry: () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 16.dp), verticalAlignment = Alignment.CenterVertically) {
        if (state.selectedId != null) TextButton(back, contentPadding = PaddingValues(end = 12.dp)) { Text("← Back") }
        Column(Modifier.weight(1f)) {
            Text("Playroom", fontSize = 25.sp, fontWeight = FontWeight.Bold)
            Text("Hey, ${state.account?.name}", color = Muted, style = MaterialTheme.typography.bodySmall)
        }
        TextButton(retry) {
            Text(if (state.online) "● Connected" else "↻ Reconnect", color = if (state.online) Mint else Violet, fontSize = 12.sp)
        }
    }
}

@Composable private fun ErrorBanner(message: String, retry: () -> Unit) {
    Surface(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp),
        shape = RoundedCornerShape(12.dp), color = Color(0xFF442F32)) {
        Row(Modifier.padding(start = 14.dp, top = 8.dp, bottom = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(message, Modifier.weight(1f), style = MaterialTheme.typography.bodySmall, color = Color(0xFFFFDAD6))
            TextButton(retry) { Text("Retry") }
        }
    }
}

@Composable private fun Lobby(state: PortalState, vm: PortalViewModel) {
    var tab by rememberSaveable { mutableIntStateOf(0) }
    var showJoin by remember { mutableStateOf(false) }
    var link by rememberSaveable { mutableStateOf("") }
    if (showJoin) AlertDialog(onDismissRequest = { showJoin = false },
        title = { Text("Open a friend's match") }, text = {
            OutlinedTextField(link, { link = it }, label = { Text("Match link or ID") }, maxLines = 3)
        }, confirmButton = { TextButton({ showJoin = false; vm.openMatch(link) }, enabled = link.isNotBlank()) { Text("Open match") } },
        dismissButton = { TextButton({ showJoin = false }) { Text("Cancel") } })
    Row(Modifier.padding(horizontal = 20.dp, vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text("Your next move", fontSize = 28.sp, fontWeight = FontWeight.Bold)
            Text("Small games. Shared moments.", color = Muted, fontSize = 13.sp)
        }
        OutlinedButton({ showJoin = true }, shape = RoundedCornerShape(12.dp)) { Text("Join by link") }
    }
    TabRow(selectedTabIndex = tab, containerColor = Ink) {
        listOf("Games", "My matches", "Open lobbies", "Builder").forEachIndexed { index, title ->
            Tab(selected = tab == index, onClick = { tab = index }, text = { Text(title, maxLines = 1) })
        }
    }
    if (state.loading) LinearProgressIndicator(Modifier.fillMaxWidth())
    if (tab == 3) {
        BuilderTab()
    } else {
        LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(20.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
            if (tab == 0) {
            if (state.games.isEmpty()) item { EmptyState("The table is being set", "Games will appear here when the server is ready.") }
            items(state.games, key = { it.id }) { game ->
                Card(shape = RoundedCornerShape(22.dp), colors = CardDefaults.cardColors(containerColor = Color(0xFF20293D))) {
                    Column(Modifier.padding(20.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Surface(color = Color(0xFF37304F), shape = RoundedCornerShape(16.dp)) {
                                Text(if (game.name.contains("tic", true)) "✕ ○" else "◇", Modifier.padding(16.dp), fontSize = 28.sp, color = Violet)
                            }
                            Spacer(Modifier.width(16.dp))
                            Column(Modifier.weight(1f)) {
                                Text(game.name, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
                                Text("${game.counts.joinToString(" / ")} players · Online", color = Mint, fontSize = 12.sp)
                            }
                        }
                        if (game.description.isNotBlank()) { Spacer(Modifier.height(16.dp)); Text(game.description, color = Muted, maxLines = 3, overflow = TextOverflow.Ellipsis) }
                        Spacer(Modifier.height(20.dp))
                        Button({ vm.createMatch(game) }, Modifier.fillMaxWidth().testTag("create-${game.id}"), enabled = !state.busy && state.online, shape = RoundedCornerShape(12.dp)) { Text("Create a match") }
                        if (2 in game.counts) TextButton({ vm.createMatch(game, 1) }, Modifier.fillMaxWidth(), enabled = !state.busy && state.online) { Text("Practice with a computer") }
                    }
                }
            }
        } else {
            val matches = (if (tab == 1) state.mine else state.open.filter { it.seat(state.account?.id) == null }).reversed()
            if (matches.isEmpty()) item { EmptyState(if (tab == 1) "Your story starts here" else "Room for a friend", if (tab == 1) "Create a game or join a friend's match." else "Create a match and share its link, or check back for an open lobby.") }
            items(matches, key = { it.id }) { match ->
                Card(onClick = { vm.openMatch(match.id) }, shape = RoundedCornerShape(18.dp)) {
                    Column(Modifier.fillMaxWidth().padding(18.dp)) {
                        Text(state.games.firstOrNull { it.id == match.gameId }?.name ?: "Game", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                        Spacer(Modifier.height(5.dp))
                        Text(match.players.joinToString(" · ") { playerName(it, state) }, color = Muted, fontSize = 13.sp)
                        Spacer(Modifier.height(12.dp))
                        Text(if (match.actingFor(state.account?.id) != null) "Your move →" else statusLabel(match.status), color = Mint, fontSize = 12.sp)
                    }
                }
            }
        }
    }
    }
}

@Composable
fun BuilderTab(vm: BuilderViewModel = viewModel()) {
    val state by vm.state.collectAsStateWithLifecycle()
    var message by rememberSaveable { mutableStateOf("") }
    val keyboard = LocalSoftwareKeyboardController.current
    var builderTab by rememberSaveable { mutableIntStateOf(0) }

    Column(Modifier.fillMaxSize()) {
        TabRow(selectedTabIndex = builderTab, containerColor = Ink) {
            listOf("Chat & Edit", "Preview").forEachIndexed { index, title ->
                Tab(selected = builderTab == index, onClick = { builderTab = index }, text = { Text(title) })
            }
        }
        
        if (builderTab == 0) {
            Column(Modifier.fillMaxSize().padding(16.dp)) {
                if (state.published) {
                    AlertDialog(
                        onDismissRequest = vm::dismissSuccess,
                        title = { Text("Success") },
                        text = { Text("Game published successfully!") },
                        confirmButton = { TextButton(vm::dismissSuccess) { Text("OK") } }
                    )
                }
                
                state.error?.let { 
                    ErrorBanner(it, vm::dismissError) 
                    Spacer(Modifier.height(8.dp))
                }

                OutlinedTextField(
                    value = state.name,
                    onValueChange = vm::updateName,
                    label = { Text("Game Name") },
                    modifier = Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(12.dp)
                )
                Spacer(Modifier.height(8.dp))
                OutlinedTextField(
                    value = state.description,
                    onValueChange = vm::updateDescription,
                    label = { Text("Game Description") },
                    modifier = Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(12.dp),
                    maxLines = 3
                )
                
                Spacer(Modifier.height(16.dp))
                LazyColumn(Modifier.weight(1f)) {
                    items(state.messages) { msg ->
                        val align = if (msg.role == "user") Alignment.End else Alignment.Start
                        val color = if (msg.role == "user") Violet else Color(0xFF20293D)
                        val textColor = if (msg.role == "user") Color(0xFF271A46) else Color.White
                        
                        Column(Modifier.fillMaxWidth().padding(vertical = 4.dp), horizontalAlignment = align) {
                            Surface(
                                color = color,
                                shape = RoundedCornerShape(12.dp)
                            ) {
                                Text(
                                    text = msg.content,
                                    color = textColor,
                                    modifier = Modifier.padding(12.dp)
                                )
                            }
                        }
                    }
                    if (state.busy) {
                        item {
                            Column(Modifier.fillMaxWidth().padding(16.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                                CircularProgressIndicator()
                                if (state.countdown > 0) {
                                    Spacer(Modifier.height(8.dp))
                                    Text("Generating... this can take up to ${state.countdown} seconds", color = Muted, fontSize = 12.sp)
                                }
                            }
                        }
                    }
                }
                
                Spacer(Modifier.height(8.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    OutlinedTextField(
                        value = message,
                        onValueChange = { message = it },
                        modifier = Modifier.weight(1f),
                        label = { Text("Message AI Agent") },
                        shape = RoundedCornerShape(24.dp),
                        enabled = !state.busy
                    )
                    Spacer(Modifier.width(8.dp))
                    Button(
                        onClick = { 
                            keyboard?.hide()
                            vm.sendMessage(message)
                            message = ""
                        },
                        enabled = message.isNotBlank() && !state.busy,
                        shape = RoundedCornerShape(24.dp)
                    ) {
                        Text("Send")
                    }
                }
                
                Spacer(Modifier.height(16.dp))
                Button(
                    onClick = vm::publish,
                    modifier = Modifier.fillMaxWidth(),
                    enabled = !state.busy && state.name.isNotBlank(),
                    shape = RoundedCornerShape(12.dp)
                ) {
                    Text("Publish Game")
                }
            }
        } else {
            // Preview Tab
            if (state.code.isBlank()) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Text("No code to preview yet. Send a message to the AI agent to build your game!", color = Muted, style = MaterialTheme.typography.bodyMedium)
                }
            } else {
                AndroidView(
                    factory = { context ->
                        WebView(context).apply {
                            settings.javaScriptEnabled = true
                            settings.domStorageEnabled = true
                            webViewClient = WebViewClient()
                            webChromeClient = WebChromeClient()
                        }
                    },
                    update = { webView ->
                        webView.loadDataWithBaseURL("https://buildplay.fun", state.code, "text/html", "UTF-8", null)
                    },
                    modifier = Modifier.fillMaxSize()
                )
            }
        }
    }
}

@Composable private fun EmptyState(title: String, text: String) {
    Column(Modifier.fillMaxWidth().padding(vertical = 48.dp), horizontalAlignment = Alignment.CenterHorizontally) {
        Text("○", fontSize = 48.sp, color = Violet)
        Spacer(Modifier.height(12.dp)); Text(title, style = MaterialTheme.typography.titleLarge)
        Spacer(Modifier.height(8.dp)); Text(text, color = Muted, style = MaterialTheme.typography.bodyMedium)
    }
}

private fun statusLabel(status: String) = when (status) { "waiting_for_players" -> "Waiting for players"; "ongoing" -> "In play"; "over" -> "Finished"; else -> status }
private fun playerName(player: Player, state: PortalState) = if (player.kind == "computer") "Computer ${player.index + 1}" else state.names[player.userId] ?: player.userId?.take(8) ?: "Player"

@Composable private fun MatchScreen(state: PortalState, vm: PortalViewModel) {
    val match = state.match ?: return
    val game = state.game ?: return
    val context = LocalContext.current
    val mine = match.seat(state.account?.id)
    val owner = match.ownerId == state.account?.id
    var confirmation by remember { mutableStateOf<String?>(null) }
    var copied by remember { mutableStateOf(false) }
    confirmation?.let { action ->
        AlertDialog(onDismissRequest = { confirmation = null }, title = { Text("$action match?") },
            text = { Text(if (action == "Delete") "This removes the match for everyone." else if (game.allowsLeave) "A computer will take over your seat." else "Leaving an ongoing match ends it for everyone.") },
            confirmButton = { TextButton({ confirmation = null; if (action == "Delete") vm.delete() else vm.leave() }) { Text(action) } },
            dismissButton = { TextButton({ confirmation = null }) { Text("Stay") } })
    }
    Column(Modifier.fillMaxSize()) {
        Column(Modifier.padding(horizontal = 20.dp)) {
            Text(game.name, fontSize = 28.sp, fontWeight = FontWeight.Bold)
            Text(statusLabel(match.status), color = Mint, fontSize = 13.sp)
            Spacer(Modifier.height(12.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                match.players.forEach { player ->
                    Surface(Modifier.weight(1f), color = if (player.index in (match.turn ?: emptyList())) Color(0xFF39304F) else Color(0xFF20293D), shape = RoundedCornerShape(12.dp)) {
                        Column(Modifier.padding(12.dp)) {
                            Text(playerName(player, state), maxLines = 1, overflow = TextOverflow.Ellipsis, fontWeight = FontWeight.SemiBold, fontSize = 13.sp)
                            Text(if (player.index in (match.turn ?: emptyList())) "To move" else if (player.userId == state.account?.id) "You" else "Player ${player.index + 1}", color = Muted, fontSize = 11.sp)
                        }
                    }
                }
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                TextButton({
                    (context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager).setPrimaryClip(ClipData.newPlainText("Match link", vm.matchLink()))
                    copied = true
                }) { Text(if (copied) "Link copied" else "Copy link") }
                TextButton({ context.startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).apply { type = "text/plain"; putExtra(Intent.EXTRA_TEXT, vm.matchLink()) }, "Invite a friend")) }) { Text("Share") }
                Spacer(Modifier.weight(1f))
                if (mine != null && match.status == "ongoing") TextButton({ confirmation = "Leave" }, enabled = !state.busy) { Text("Leave") }
                else if (owner) TextButton({ confirmation = "Delete" }, enabled = !state.busy) { Text("Delete") }
                else if (mine != null && match.status == "waiting_for_players") TextButton(vm::leave, enabled = !state.busy) { Text("Leave") }
                else if (mine != null && match.status == "over") TextButton(vm::delete, enabled = !state.busy) { Text("Hide") }
            }
        }
        if (state.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
        if (match.status == "waiting_for_players") {
            Column(Modifier.fillMaxSize().padding(24.dp), verticalArrangement = Arrangement.Center, horizontalAlignment = Alignment.CenterHorizontally) {
                Text("Take a seat", fontSize = 30.sp, fontWeight = FontWeight.Bold)
                Spacer(Modifier.height(12.dp))
                Text("${match.players.size} seated · ${game.counts.joinToString(" or ")} needed", color = Muted)
                Spacer(Modifier.height(22.dp))
                if (mine == null && match.players.size < game.counts.max()) Button(vm::join, enabled = !state.busy && state.online) { Text("Join this match") }
                else if (owner) {
                    Button(vm::start, enabled = !state.busy && state.online && match.players.size in game.counts, modifier = Modifier.fillMaxWidth().height(52.dp)) { Text("Start game") }
                    val computers = match.players.count { it.kind == "computer" }
                    Row {
                        TextButton({ vm.computers(computers - 1) }, enabled = computers > 0 && !state.busy) { Text("− Computer") }
                        TextButton({ vm.computers(computers + 1) }, enabled = match.players.size < game.counts.max() && !state.busy) { Text("+ Computer") }
                    }
                } else Text("The host will start when everyone is here.", color = Muted)
                Spacer(Modifier.height(16.dp))
                Text("Send the link to someone on another device. They'll join the same game.", color = Muted, fontSize = 13.sp)
            }
        } else {
            if (mine == null && match.status == "ongoing" && game.allowsJoin && (match.players.any { it.kind == "computer" } || match.players.size < game.counts.max())) {
                Button(vm::join, Modifier.padding(horizontal = 20.dp), enabled = !state.busy && state.online) { Text("Join this match") }
            }
            GameView(state, Modifier.fillMaxWidth().weight(1f).padding(horizontal = 12.dp, vertical = 4.dp), vm::makeMove, vm::reportError)
            if (match.status == "over") Button({ vm.createMatch(game) }, Modifier.fillMaxWidth().padding(16.dp), enabled = !state.busy && state.online && !game.deleted) { Text("Play again") }
        }
    }
}
