package team.bjtuss.bjtuselfservice.shared.feature.courseware

import java.io.File
import java.nio.file.Files
import java.security.MessageDigest
import java.util.zip.ZipInputStream
import kotlinx.coroutines.*
import kotlinx.serialization.json.*
import team.bjtuss.bjtuselfservice.shared.data.homework.isSchoolPlaybackStreamUrl

internal actual fun localTranscriptionAvailable(): Boolean = System.getProperty("os.name").startsWith("Windows", ignoreCase = true)

/** Isolated Python worker; request goes over stdin, never command-line arguments or logs. */
internal actual suspend fun transcribeLocally(url: String, title: String, onStatus: (String) -> Unit): TranscriptResult = withContext(Dispatchers.IO) {
    require(localTranscriptionAvailable() && isSchoolPlaybackStreamUrl(url))
    val home = System.getenv("LOCALAPPDATA")?.let { File(it, "BJTUTranscript") }
        ?: throw LocalTranscriptException("无法找到 Windows 用户应用目录。")
    withContext(Dispatchers.Main) { onStatus("正在准备内置语音识别组件…") }
    val worker = TranscriptRuntime.executable(home)
    val work = Files.createTempDirectory("bjtu-transcript-").toFile()
    var process: Process? = null
    try {
        val child = ProcessBuilder(worker.absolutePath, work.absolutePath)
            .apply { environment()["PYTHONIOENCODING"] = "utf-8" }
            .redirectError(ProcessBuilder.Redirect.DISCARD).start()
        process = child
        var failureMessage: String? = null
        coroutineScope {
            val watcher = launch(Dispatchers.Default, start = CoroutineStart.UNDISPATCHED) {
                try { awaitCancellation() } finally {
                    if (child.isAlive) withContext(NonCancellable + Dispatchers.IO) {
                        // Include FFmpeg descendants; stopping only Python leaves audio extraction running.
                        runCatching { ProcessBuilder("taskkill.exe", "/PID", child.pid().toString(), "/T", "/F").redirectOutput(ProcessBuilder.Redirect.DISCARD).redirectError(ProcessBuilder.Redirect.DISCARD).start().waitFor() }
                        if (child.isAlive) child.destroyForcibly()
                    }
                }
            }
            try {
                child.outputStream.bufferedWriter(Charsets.UTF_8).use {
                    it.write(buildJsonObject { put("url", url); put("title", title) }.toString())
                    it.newLine()
                }
                child.inputStream.bufferedReader(Charsets.UTF_8).useLines { lines ->
                    for (line in lines) {
                        ensureActive()
                        val event = runCatching { Json.parseToJsonElement(line).jsonObject }.getOrNull()
                        val message = event?.get("status")?.jsonPrimitive?.content
                        if (event?.get("failed")?.jsonPrimitive?.booleanOrNull == true) failureMessage = message
                        if (message != null) withContext(Dispatchers.Main) { onStatus(message.take(300)) }
                    }
                }
                ensureActive()
                if (child.waitFor() != 0) throw LocalTranscriptException(failureMessage ?: "转写程序异常退出，请重新启动应用后重试。")
            } finally { withContext(NonCancellable) { watcher.cancelAndJoin() } }
        }
        val txt = File(work, "transcript.txt")
        val srt = File(work, "transcript.srt")
        if (!txt.isFile || !srt.isFile || txt.length() > 16 * 1024 * 1024 || srt.length() > 16 * 1024 * 1024) {
            throw LocalTranscriptException("未生成有效文字稿，请重试。")
        }
        TranscriptResult(txt.readText(Charsets.UTF_8), srt.readText(Charsets.UTF_8))
    } finally {
        withContext(NonCancellable) {
            process?.let { child -> if (child.isAlive) child.destroyForcibly(); runCatching { child.waitFor() } }
            work.deleteRecursively()
        }
    }
}

/** Extract the bundled standalone worker once per content hash, with zip-slip protection. */
private object TranscriptRuntime {
    private var cached: File? = null

    @Synchronized
    fun executable(home: File): File {
        cached?.takeIf(File::isFile)?.let { return it }
        home.mkdirs()
        val archive = File.createTempFile("runtime-", ".zip", home)
        var staging: File? = null
        try {
            val digest = MessageDigest.getInstance("SHA-256")
            val source = javaClass.getResourceAsStream("/transcription/runtime.zip")
                ?: throw LocalTranscriptException("安装包缺少内置识别组件，请重新安装文字稿版。")
            source.use { input -> archive.outputStream().use { out ->
                val buffer = ByteArray(65536)
                while (true) {
                    val n = input.read(buffer)
                    if (n < 0) break
                    digest.update(buffer, 0, n)
                    out.write(buffer, 0, n)
                }
            } }
            val hash = digest.digest().joinToString("") { "%02x".format(it.toInt() and 255) }
            val target = File(home, "runtime-$hash")
            val exe = File(target, "BJTUTranscriptWorker.exe")
            if (!File(target, ".complete").isFile || !exe.isFile) {
                val temp = Files.createTempDirectory(home.toPath(), "extract-").toFile()
                staging = temp
                ZipInputStream(archive.inputStream().buffered()).use { zip ->
                    while (true) {
                        val entry = zip.nextEntry ?: break
                        val file = File(temp, entry.name).canonicalFile
                        require(file.toPath().startsWith(temp.canonicalFile.toPath()))
                        if (entry.isDirectory) file.mkdirs() else {
                            file.parentFile.mkdirs()
                            file.outputStream().use { zip.copyTo(it) }
                        }
                        zip.closeEntry()
                    }
                }
                check(File(temp, "BJTUTranscriptWorker.exe").isFile)
                File(temp, ".complete").writeText(hash)
                if (target.exists()) target.deleteRecursively()
                Files.move(temp.toPath(), target.toPath())
            }
            cached = exe
            return exe
        } finally {
            archive.delete()
            staging?.deleteRecursively()
        }
    }
}
