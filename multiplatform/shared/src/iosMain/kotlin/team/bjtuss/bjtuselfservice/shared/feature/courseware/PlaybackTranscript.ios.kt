package team.bjtuss.bjtuselfservice.shared.feature.courseware

internal actual fun localTranscriptionAvailable(): Boolean = false
internal actual suspend fun transcribeLocally(url: String, title: String, onStatus: (String) -> Unit): TranscriptResult =
    throw LocalTranscriptException("本机转写仅支持 Windows。")
