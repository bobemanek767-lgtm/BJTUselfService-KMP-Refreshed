package team.bjtuss.bjtuselfservice.shared.feature.courseware

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import team.bjtuss.bjtuselfservice.shared.domain.courseware.PlaybackStream
import team.bjtuss.bjtuselfservice.shared.files.*

internal data class TranscriptResult(val txt: String, val srt: String)
internal expect fun localTranscriptionAvailable(): Boolean
internal expect suspend fun transcribeLocally(url: String, title: String, onStatus: (String) -> Unit): TranscriptResult

@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun PlaybackTranscriptPanel(streams: List<PlaybackStream>, title: String, fileGateway: FileGateway) {
    if (!localTranscriptionAvailable()) return
    var expanded by remember { mutableStateOf(false) }
    var status by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var result by remember { mutableStateOf<TranscriptResult?>(null) }
    var operation by remember { mutableStateOf<Job?>(null) }
    val scope = rememberCoroutineScope()
    TextButton(onClick = { expanded = !expanded }) { Text(if (expanded) "收起语音转文字" else "生成课堂文字稿") }
    if (!expanded) return
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text("在本机运行中文语音识别，不需要手机、连接地址或密钥。安装包已内置识别环境，首次转写需下载模型；电脑需能访问校园录像。", style = MaterialTheme.typography.bodySmall)
        Text("只处理所选的第一路画面，请选择含教师声音的录像。离开本课节会取消正在进行的转写，完成后请及时导出。", style = MaterialTheme.typography.bodySmall)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            OutlinedButton(enabled = !busy && streams.isNotEmpty(), onClick = {
                busy = true
                result = null
                status = "正在准备本机转写…"
                val url = streams.first().hlsUrl
                operation = scope.launch {
                    try {
                        result = transcribeLocally(url, title) { status = it }
                        status = "文字稿已生成，可导出 TXT 或 SRT"
                    } catch (e: CancellationException) { throw e }
                    catch (e: LocalTranscriptException) { status = e.message.orEmpty() }
                    catch (_: Exception) { status = "转写失败，请检查本机转写环境和校园网后重试。" }
                    finally { busy = false }
                }
            }) { Text("在本机生成文字稿") }
            if (busy) TextButton(onClick = { operation?.cancel(); status = "已取消转写" }) { Text("取消转写") }
        }
        if (busy) LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
        if (status.isNotBlank()) Text(status, style = MaterialTheme.typography.bodySmall)
        val completed = result
        if (completed != null) FlowRow(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            listOf("txt", "srt").forEach { format ->
                OutlinedButton(enabled = !busy && fileGateway.isAvailable, onClick = {
                    busy = true
                    operation = scope.launch {
                        try {
                            val name = title.replace(Regex("[\\\\/:*?\"<>|\\p{Cntrl}]"), "_").trim().trimEnd('.').take(100).ifBlank { "课堂文字稿" }
                            val text = if (format == "txt") completed.txt else completed.srt
                            status = when (fileGateway.saveFile(FileContent("课堂文字稿_$name.$format", "text/plain", text.encodeToByteArray()))) {
                                FileSaveResult.Saved -> "文字稿已保存"
                                FileSaveResult.Cancelled -> "已取消保存，可再次导出"
                                is FileSaveResult.Failed -> "保存失败，请换一个位置重试"
                            }
                        } catch (e: CancellationException) { throw e }
                        catch (_: Exception) { status = "导出失败，请重试" }
                        finally { busy = false }
                    }
                }) { Text("导出 ${format.uppercase()}") }
            }
        }
    }
}

internal class LocalTranscriptException(message: String) : Exception(message)
