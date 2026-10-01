package com.terraducktel.jetbrains.output

import com.terraducktel.jetbrains.api.PLAN_LANDED_STATUSES
import com.terraducktel.jetbrains.api.Run
import com.terraducktel.jetbrains.api.RunStep
import com.terraducktel.jetbrains.api.TdtClient

/** Sink for one line of run output — implemented by a real console in [RunConsoles], and by a
 *  plain list in tests. */
fun interface LineSink {
    fun appendLine(line: String)
}

/**
 * Blocking, line-for-line port of `services/vscode/src/output/runOutput.ts`'s `tailRun`: follows
 * a run's steps, printing each step's header exactly once and only the output lines not already
 * printed, re-polling from the first not-yet-finished step's position (`since`) so its growing
 * output keeps arriving without ever reprinting a finished step. Stops when the run lands
 * (terminal or `awaiting_approval`), [isCancelled] flips, or [timeoutMs] elapses — in every case
 * returning the last [Run] observed.
 *
 * Every `sink.appendLine` call is preceded by a cancellation check taken AFTER the network call
 * that produced the data: the sink may back a console the user has already asked to stop
 * following, and even when it survives, a cancelled watch must not keep printing lines.
 */
object RunTail {

    fun tail(
        client: TdtClient,
        runId: String,
        sink: LineSink,
        pollMs: Long = 2000,
        isCancelled: () -> Boolean = { false },
        timeoutMs: Long = 3 * 3600_000L,
    ): Run {
        val deadline = System.currentTimeMillis() + timeoutMs
        var since = 0
        val headerPrinted = HashSet<Int>()
        val printedCount = HashMap<Int, Int>() // position -> number of lines already printed

        fun linesOf(output: String?): List<String> {
            val norm = (output ?: "").trimEnd('\n')
            return if (norm.isEmpty()) emptyList() else norm.split("\n")
        }

        fun applySteps(steps: List<RunStep>) {
            val sorted = steps.sortedBy { it.position }
            for (st in sorted) {
                if (st.position !in headerPrinted) {
                    sink.appendLine("── ${st.name} [${st.status}]")
                    headerPrinted += st.position
                    printedCount[st.position] = 0
                }
                val lines = linesOf(st.output)
                val done = printedCount[st.position] ?: 0
                if (lines.size > done) {
                    for (l in lines.subList(done, lines.size)) sink.appendLine(l)
                    printedCount[st.position] = lines.size
                }
            }
            if (sorted.isNotEmpty()) {
                val firstUnfinished = sorted.find { it.status == "pending" || it.status == "running" }
                since = firstUnfinished?.position ?: (sorted.last().position + 1)
            }
        }

        fun cancelled() = isCancelled()

        var latest: Run? = null
        while (true) {
            val steps = client.getSteps(runId, since)
            if (cancelled()) return latest ?: client.getRun(runId)
            applySteps(steps)
            val run = client.getRun(runId)
            latest = run
            if (run.status in PLAN_LANDED_STATUSES) {
                // The steps fetched above may still lag one status transition behind the run
                // itself (the run flips to its landed status between our steps call and our run
                // call) — do one last flush so the final step output/status makes it into the
                // sink before we return.
                val last = client.getSteps(runId, since)
                if (cancelled()) return run
                applySteps(last)
                sink.appendLine("── run ${run.status}")
                return run
            }
            if (cancelled() || System.currentTimeMillis() > deadline) return run
            Thread.sleep(pollMs)
            if (cancelled()) return run // cancelled while we slept — stop before the next request
        }
    }
}
