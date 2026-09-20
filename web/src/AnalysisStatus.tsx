import { useEffect, useState } from "react";
import { api } from "./api";
import type { DocumentRecord } from "./api";

type AnalysisStatus = {
  worker: { online: boolean; last_seen: number | null; capacity: number };
  queue: { waiting: number; analyzing: number; retrying: number };
  model: { state: string; message: string; checked_at: number; name: string };
};

export function useAnalysisStatus(enabled: boolean) {
  const [status, setStatus] = useState<AnalysisStatus | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    if (!enabled) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      try {
        const next = await api<AnalysisStatus>("/analysis-status");
        if (!stopped) {
          setStatus(next);
          setError(false);
        }
      } catch {
        if (!stopped) setError(true);
      } finally {
        if (!stopped) timer = setTimeout(refresh, 6000);
      }
    };
    void refresh();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [enabled]);
  return { status: error ? null : status, error };
}

export const analysisLabel = (status: string) =>
  ({
    queued: "Waiting for analysis",
    extracting: "Analyzing",
    ready: "Analysis complete",
    failed: "Analysis failed",
  })[status] || status;

export function waitingReason(
  doc: DocumentRecord,
  status: AnalysisStatus | null,
) {
  if (doc.status === "extracting" && status && !status.worker.online)
    return "No recent worker heartbeat. Analysis may be interrupted; the worker will recover it automatically when available.";
  if (doc.status !== "queued") return null;
  if (doc.analysis?.retry_at && doc.analysis.retry_at > Date.now() / 1000)
    return `Analysis will retry automatically after ${new Date(doc.analysis.retry_at * 1000).toLocaleTimeString()}.`;
  if (!status)
    return "Checking analysis availability. Your document is saved and will be analyzed automatically.";
  if (!status.worker.online)
    return "No recent worker heartbeat. Analysis will start automatically when the worker is available.";
  if (status.model.state === "unreachable")
    return "The model connection needs attention. The worker will attempt analysis automatically.";
  if (status.queue.analyzing >= status.worker.capacity)
    return "The worker is analyzing other documents. This document will start when a slot is available.";
  return "The worker will pick up this document automatically.";
}

export function AnalysisStatusPanel({
  status,
  error,
  onSettings,
}: ReturnType<typeof useAnalysisStatus> & { onSettings: () => void }) {
  return (
    <section className="analysis-status" aria-label="Document analysis status">
      <div>
        <strong>Document analysis</strong>
        <p>
          Uploads are analyzed automatically. Quicker detects the document type
          for you.
        </p>
        {error ? (
          <p role="status">
            Analysis status is unavailable. Uploaded documents remain saved.
          </p>
        ) : !status ? (
          <p role="status">Checking worker and model connection…</p>
        ) : (
          <>
            <div className="analysis-indicators" role="status">
              <span>
                <i className={status.worker.online ? "dot green" : "dot"} />{" "}
                Worker:{" "}
                {status.worker.online ? "Online" : "No recent heartbeat"}
              </span>
              <span>
                <i
                  className={
                    status.model.state === "connected" ? "dot green" : "dot"
                  }
                />{" "}
                Model connection:{" "}
                {status.model.state === "connected"
                  ? "Connected"
                  : status.model.state === "unreachable"
                    ? "Needs attention"
                    : "Not verified"}
              </span>
              <span>
                {status.queue.waiting} waiting · {status.queue.analyzing}{" "}
                analyzing
              </span>
            </div>
            {!status.worker.online && (
              <p>
                Start or restart the analysis worker on the server. Saved
                documents stay in the queue.
              </p>
            )}
            {status.model.state !== "connected" && (
              <p>{status.model.message}</p>
            )}
            <details>
              <summary>Connection details</summary>
              <p>
                Model: {status.model.name}. Checked{" "}
                {new Date(status.model.checked_at * 1000).toLocaleTimeString()}.
              </p>
              <p>
                The connection check verifies server availability. The vision
                check in Settings also verifies that the selected model can read
                images.
              </p>
            </details>
          </>
        )}
      </div>
      <button onClick={onSettings}>Model settings</button>
    </section>
  );
}
