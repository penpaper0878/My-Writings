@file:OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)

package io.github.penpaper0878.pdf2md.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import io.github.penpaper0878.pdf2md.convert.ModelStore
import io.github.penpaper0878.pdf2md.convert.Reader
import io.github.penpaper0878.pdf2md.convert.Settings
import kotlinx.coroutines.delay

class Actions(
    val pick: () -> Unit,
    val scan: () -> Unit,
    val save: () -> Unit,
    val share: () -> Unit,
    val copy: () -> Unit,
)

@Composable
fun App(vm: AppViewModel, state: UiState, actions: Actions) {
    Scaffold(topBar = { TopAppBar(title = { Text("pdf2md") }) }) { padding ->
        Column(Modifier.padding(padding).fillMaxSize()) {
            when (val stage = state.stage) {
                is Stage.Idle -> Home(vm, state, actions)
                is Stage.Working -> Working(stage, onCancel = vm::cancel)
                is Stage.Done -> Done(stage, actions, onNew = vm::reset)
                is Stage.Failed -> Failed(stage, onBack = vm::reset)
                is Stage.NeedPassword -> {
                    Home(vm, state, actions)
                    PasswordDialog(stage, onOpen = vm::retryWithPassword, onCancel = vm::reset)
                }
            }
        }
    }
}

@Composable
private fun Section(title: String, content: @Composable () -> Unit) {
    Card(Modifier.fillMaxWidth(), colors = CardDefaults.cardColors()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(title, style = MaterialTheme.typography.titleMedium)
            content()
        }
    }
}

@Composable
private fun Home(vm: AppViewModel, state: UiState, actions: Actions) {
    val s = state.settings
    Column(
        Modifier.verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Text(
            "Typed PDFs are converted exactly from their text. Scanned and handwritten pages are read " +
                "by the reader chosen below. Nothing is uploaded to the internet.",
            style = MaterialTheme.typography.bodyMedium,
        )
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Button(onClick = actions.pick) { Text("Choose PDF or photos") }
            OutlinedButton(onClick = actions.scan) { Text("Scan pages") }
        }

        Section("Reading handwriting and scans") {
            ReaderChoice(
                selected = s.reader == Reader.PHONE,
                title = "On this phone (AI)",
                detail = "Private and offline. One-time download of about ${"%.1f".format(vm.modelSizeMb / 1024.0)} GB. " +
                    "Slower than a computer; how long a page takes depends on the phone.",
                onSelect = { vm.updateSettings { it.copy(reader = Reader.PHONE) } },
            )
            if (s.reader == Reader.PHONE) ModelPanel(vm, state.model)
            ReaderChoice(
                selected = s.reader == Reader.COMPUTER,
                title = "My computer (Ollama)",
                detail = "Most accurate. A PC on the same Wi-Fi runs Ollama with a vision model.",
                onSelect = { vm.updateSettings { it.copy(reader = Reader.COMPUTER) } },
            )
            if (s.reader == Reader.COMPUTER) ComputerPanel(vm, s, state.computerCheck)
            ReaderChoice(
                selected = s.reader == Reader.PRINTED,
                title = "Printed text only",
                detail = "Fast, no download. Fine for printed scans; not for handwriting.",
                onSelect = { vm.updateSettings { it.copy(reader = Reader.PRINTED) } },
            )
        }

        Section("Options") { Options(vm, s) }

        Text(
            "Tip: share a PDF or photos to pdf2md from WhatsApp, Files or Gallery.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun ReaderChoice(selected: Boolean, title: String, detail: String, onSelect: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().selectable(selected = selected, onClick = onSelect, role = Role.RadioButton),
        verticalAlignment = Alignment.Top,
    ) {
        RadioButton(selected = selected, onClick = null, modifier = Modifier.padding(top = 2.dp, end = 12.dp))
        Column {
            Text(title, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.Medium)
            Text(detail, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun ModelPanel(vm: AppViewModel, status: ModelStore.Status) {
    if (status is ModelStore.Status.Downloading) {
        LaunchedEffect(status) {
            delay(1000)
            vm.refreshModel()
        }
    }
    Column(Modifier.padding(start = 48.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        when (status) {
            is ModelStore.Status.Ready -> Row(verticalAlignment = Alignment.CenterVertically) {
                Text("AI model ready.", Modifier.weight(1f))
                TextButton(onClick = vm::removeModel) { Text("Remove") }
            }
            is ModelStore.Status.Downloading -> {
                val fraction = if (status.total > 0) status.done.toFloat() / status.total else 0f
                Text("Downloading… ${status.done / 1_048_576} of ${status.total / 1_048_576} MB")
                LinearProgressIndicator(progress = { fraction }, modifier = Modifier.fillMaxWidth())
                Text(
                    "It continues in the background; you can leave the app.",
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            is ModelStore.Status.Failed -> {
                Text(status.reason, color = MaterialTheme.colorScheme.error)
                DownloadButtons(vm)
            }
            is ModelStore.Status.Missing -> {
                Text("The AI model is not downloaded yet.")
                DownloadButtons(vm)
            }
        }
    }
}

@Composable
private fun DownloadButtons(vm: AppViewModel) {
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Button(onClick = { vm.downloadModel(wifiOnly = true) }) { Text("Download on Wi-Fi") }
        OutlinedButton(onClick = { vm.downloadModel(wifiOnly = false) }) { Text("Use mobile data") }
    }
}

@Composable
private fun ComputerPanel(vm: AppViewModel, s: Settings, check: String?) {
    Column(Modifier.padding(start = 48.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        OutlinedTextField(
            value = s.computerUrl,
            onValueChange = { v -> vm.updateSettings { it.copy(computerUrl = v.trim()) } },
            label = { Text("Computer's address") },
            placeholder = { Text("192.168.1.20") },
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = s.computerModel,
            onValueChange = { v -> vm.updateSettings { it.copy(computerModel = v.trim()) } },
            label = { Text("Model") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        Row(verticalAlignment = Alignment.CenterVertically) {
            OutlinedButton(onClick = vm::testComputer, enabled = s.computerUrl.isNotBlank()) { Text("Test connection") }
        }
        if (check != null) Text(check, style = MaterialTheme.typography.bodySmall)
        Text(
            "On the computer: install Ollama, run \"ollama pull ${s.computerModel}\", and start it with " +
                "OLLAMA_HOST=0.0.0.0 so the phone can reach it.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

private val LANGUAGES = listOf(
    "eng" to "English", "hin" to "Hindi", "guj" to "Gujarati", "mar" to "Marathi", "ben" to "Bengali",
    "tam" to "Tamil", "tel" to "Telugu", "kan" to "Kannada", "mal" to "Malayalam", "pan" to "Punjabi",
    "urd" to "Urdu", "san" to "Sanskrit",
)

@Composable
private fun Options(vm: AppViewModel, s: Settings) {
    val chosen = s.lang.split("+").filter { it.isNotBlank() }
    Text("Languages on the pages", style = MaterialTheme.typography.bodyMedium)
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        for ((code, name) in LANGUAGES) {
            FilterChip(
                selected = code in chosen,
                onClick = {
                    val next = if (code in chosen) chosen - code else chosen + code
                    vm.updateSettings { it.copy(lang = next.joinToString("+")) }
                },
                label = { Text(name) },
            )
        }
    }
    OutlinedTextField(
        value = s.hint,
        onValueChange = { v -> vm.updateSettings { it.copy(hint = v) } },
        label = { Text("What the notes are about (optional)") },
        placeholder = { Text("e.g. class 10 chemistry") },
        modifier = Modifier.fillMaxWidth(),
    )
    OutlinedTextField(
        value = s.pages,
        onValueChange = { v -> vm.updateSettings { it.copy(pages = v) } },
        label = { Text("Pages (optional)") },
        placeholder = { Text("e.g. 1-5,8") },
        singleLine = true,
        modifier = Modifier.fillMaxWidth(),
    )
    Text("Read pages", style = MaterialTheme.typography.bodyMedium)
    for ((mode, label) in listOf(
        "auto" to "Automatically (best for each page)",
        "digital" to "Typed text only (skip scans)",
        "ocr" to "All from the page image",
    )) {
        ReaderChoice(selected = s.mode == mode, title = label, detail = "", onSelect = { vm.updateSettings { it.copy(mode = mode) } })
    }
    Text("Read each page in ${s.tiles} strip${if (s.tiles > 1) "s" else ""} (more helps small, dense handwriting)")
    Slider(
        value = s.tiles.toFloat(),
        onValueChange = { v -> vm.updateSettings { it.copy(tiles = v.toInt().coerceIn(1, 4)) } },
        valueRange = 1f..4f,
        steps = 2,
    )
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text("Mark where each page starts", Modifier.weight(1f))
        Switch(checked = s.pageMarkers, onCheckedChange = { v -> vm.updateSettings { it.copy(pageMarkers = v) } })
    }
}

@Composable
private fun Working(stage: Stage.Working, onCancel: () -> Unit) {
    Column(Modifier.padding(24.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
        Text(stage.title, style = MaterialTheme.typography.titleMedium)
        val p = stage.progress
        if (p.total > 0) {
            LinearProgressIndicator(progress = { p.done.toFloat() / p.total }, modifier = Modifier.fillMaxWidth())
            Text("${p.done} of ${p.total} pages")
        } else {
            CircularProgressIndicator()
        }
        Text(p.message, style = MaterialTheme.typography.bodyMedium)
        Text(
            "Keep pdf2md open. Pages already read are remembered, so an interrupted conversion resumes quickly.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        OutlinedButton(onClick = onCancel) { Text("Cancel") }
    }
}

private const val PREVIEW_CHARS = 60_000

@Composable
private fun Done(stage: Stage.Done, actions: Actions, onNew: () -> Unit) {
    val r = stage.result
    Column(Modifier.verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(stage.title, style = MaterialTheme.typography.titleMedium)
        Text("${r.summary()} in ${"%.0f".format(r.seconds)} s", style = MaterialTheme.typography.bodyMedium)
        r.reader?.let { Text("Reader: $it", style = MaterialTheme.typography.bodySmall) }
        if (r.warnings.isNotEmpty()) {
            Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer)) {
                Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    for (w in r.warnings.take(8)) Text("• $w", style = MaterialTheme.typography.bodySmall)
                    if (r.warnings.size > 8) Text("…and ${r.warnings.size - 8} more", style = MaterialTheme.typography.bodySmall)
                }
            }
        }
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = actions.save) { Text("Save .md") }
            OutlinedButton(onClick = actions.share) { Text("Share") }
            OutlinedButton(onClick = actions.copy) { Text("Copy") }
            TextButton(onClick = onNew) { Text("Convert another") }
        }
        Text(
            "Words the reader was unsure of are marked [?] or [illegible].",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        HorizontalDivider()
        SelectionContainer {
            Text(
                if (r.markdown.length > PREVIEW_CHARS) r.markdown.take(PREVIEW_CHARS) + "\n\n… (preview cut; Save or Share has everything)"
                else r.markdown.ifEmpty { "(no text found)" },
                fontFamily = FontFamily.Monospace,
                style = MaterialTheme.typography.bodySmall,
            )
        }
        Spacer(Modifier.height(24.dp))
    }
}

@Composable
private fun Failed(stage: Stage.Failed, onBack: () -> Unit) {
    Column(Modifier.padding(24.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
        Text(stage.title, style = MaterialTheme.typography.titleMedium)
        Text(stage.message, color = MaterialTheme.colorScheme.error)
        Button(onClick = onBack) { Text("Back") }
    }
}

@Composable
private fun PasswordDialog(stage: Stage.NeedPassword, onOpen: (String) -> Unit, onCancel: () -> Unit) {
    var password by rememberSaveable { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = onCancel,
        title = { Text(if (stage.wrong) "Wrong password" else "Password needed") },
        text = {
            Column {
                Text("${stage.title} is protected by a password.")
                Spacer(Modifier.height(8.dp))
                OutlinedTextField(
                    value = password,
                    onValueChange = { password = it },
                    singleLine = true,
                    visualTransformation = PasswordVisualTransformation(),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                )
            }
        },
        confirmButton = { TextButton(onClick = { onOpen(password) }, enabled = password.isNotEmpty()) { Text("Open") } },
        dismissButton = { TextButton(onClick = onCancel) { Text("Cancel") } },
    )
}
