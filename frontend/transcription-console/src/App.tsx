import { useEffect, useMemo, useState } from "react";

type SessionRecord = {
  id: string;
  status: string;
  started_at_utc: string;
  stopped_at_utc: string | null;
  device_name: string | null;
  model_name: string;
  language: string;
  display_connected: boolean | null;
  last_error: string | null;
};

type SegmentRecord = {
  id: string;
  session_id: string;
  sequence_no: number;
  started_at_utc: string;
  ended_at_utc: string;
  text: string;
  oled_text: string;
  created_at_utc: string;
};

type StatusPayload = {
  service_state: string;
  mic_state: string;
  display_state: string;
  display_connected: boolean | null;
  last_error: string | null;
  last_display_error: string | null;
  current_partial: string;
  current_oled_text: string;
  current_session: SessionRecord | null;
  engine_backend: string | null;
  engine_model: string | null;
};

type SessionDetail = {
  session: SessionRecord;
  segments: SegmentRecord[];
};

type AudioDevice = {
  id: string;
  name: string;
  is_default: boolean;
};

const EMPTY_STATUS: StatusPayload = {
  service_state: "idle",
  mic_state: "idle",
  display_state: "unknown",
  display_connected: null,
  last_error: null,
  last_display_error: null,
  current_partial: "",
  current_oled_text: "",
  current_session: null,
  engine_backend: null,
  engine_model: null,
};

async function readJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body.detail || "Request failed.");
  }
  return body as T;
}

function formatDateTime(value: string | null) {
  if (!value) {
    return "Active";
  }
  return new Date(value).toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function statusTone(status: StatusPayload) {
  if (status.last_error) {
    return "danger";
  }
  if (status.display_state !== "connected") {
    return "warn";
  }
  if (status.service_state === "running") {
    return "live";
  }
  return "idle";
}

export default function App() {
  const [status, setStatus] = useState<StatusPayload>(EMPTY_STATUS);
  const [sessions, setSessions] = useState<SessionRecord[]>([]);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [selectedDetail, setSelectedDetail] = useState<SessionDetail | null>(null);
  const [devices, setDevices] = useState<AudioDevice[]>([]);
  const [selectedDeviceId, setSelectedDeviceId] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [actionError, setActionError] = useState<string | null>(null);

  const activeSessionId = status.current_session?.id || null;
  const effectiveSessionId = activeSessionId || selectedSessionId;

  async function refreshSessions() {
    const nextSessions = await readJson<SessionRecord[]>("/api/sessions?limit=20");
    setSessions(nextSessions);
    if (!selectedSessionId && nextSessions[0]) {
      setSelectedSessionId(nextSessions[0].id);
    }
  }

  async function refreshSessionDetail(sessionId: string) {
    const detail = await readJson<SessionDetail>(`/api/sessions/${sessionId}`);
    setSelectedDetail(detail);
  }

  useEffect(() => {
    let closed = false;
    const source = new EventSource("/api/events");

    source.addEventListener("status", (event) => {
      const payload = JSON.parse((event as MessageEvent).data) as StatusPayload;
      if (!closed) {
        setStatus(payload);
      }
    });

    source.addEventListener("session_started", (event) => {
      const payload = JSON.parse((event as MessageEvent).data) as SessionRecord;
      if (closed) {
        return;
      }
      setSelectedSessionId(payload.id);
      void refreshSessions();
      void refreshSessionDetail(payload.id);
    });

    source.addEventListener("final_segment", (event) => {
      const payload = JSON.parse((event as MessageEvent).data) as SegmentRecord;
      if (closed) {
        return;
      }
      setSelectedDetail((current) => {
        if (!current || current.session.id !== payload.session_id) {
          return current;
        }
        return {
          ...current,
          segments: [...current.segments, payload],
        };
      });
      void refreshSessions();
    });

    source.addEventListener("session_stopped", () => {
      if (closed) {
        return;
      }
      void refreshSessions();
      if (selectedSessionId) {
        void refreshSessionDetail(selectedSessionId);
      }
    });

    return () => {
      closed = true;
      source.close();
    };
  }, [selectedSessionId]);

  useEffect(() => {
    async function bootstrap() {
      try {
        const [nextStatus, nextSessions, nextDevices] = await Promise.all([
          readJson<StatusPayload>("/api/status"),
          readJson<SessionRecord[]>("/api/sessions?limit=20"),
          readJson<AudioDevice[]>("/api/audio-devices"),
        ]);
        setStatus(nextStatus);
        setSessions(nextSessions);
        setDevices(nextDevices);
        const preferredDevice = nextDevices.find((device) => device.is_default) || nextDevices[0];
        if (preferredDevice) {
          setSelectedDeviceId(preferredDevice.id);
        }
        if (nextStatus.current_session?.id) {
          setSelectedSessionId(nextStatus.current_session.id);
          await refreshSessionDetail(nextStatus.current_session.id);
        } else if (nextSessions[0]) {
          setSelectedSessionId(nextSessions[0].id);
          await refreshSessionDetail(nextSessions[0].id);
        }
      } catch (error) {
        setActionError(error instanceof Error ? error.message : "Unable to load the console.");
      } finally {
        setLoading(false);
      }
    }

    void bootstrap();
  }, []);

  useEffect(() => {
    if (!effectiveSessionId) {
      return;
    }
    void refreshSessionDetail(effectiveSessionId);
  }, [effectiveSessionId]);

  const liveSegments = useMemo(() => {
    if (!selectedDetail) {
      return [];
    }
    return selectedDetail.segments.slice().reverse();
  }, [selectedDetail]);

  async function handleStart() {
    setActionError(null);
    try {
      await readJson<SessionRecord>("/api/session/start", {
        method: "POST",
        body: JSON.stringify({
          device_id: selectedDeviceId || undefined,
        }),
      });
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Unable to start transcription.");
    }
  }

  async function handleStop() {
    setActionError(null);
    try {
      await readJson("/api/session/stop", {
        method: "POST",
      });
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Unable to stop transcription.");
    }
  }

  if (loading) {
    return <main className="shell loading">Loading transcription console...</main>;
  }

  return (
    <main className="shell">
      <header className="hero">
        <div>
          <p className="eyebrow">clear-oled / live captions</p>
          <h1>Transcribe the Mac mic and mirror it to the OLED.</h1>
        </div>
        <div className={`status-card tone-${statusTone(status)}`}>
          <span className="status-label">Service</span>
          <strong>{status.service_state}</strong>
          <span>{status.engine_backend || "Whisper backend idle"}</span>
        </div>
      </header>

      <section className="grid">
        <article className="panel control-panel">
          <div className="panel-head">
            <p className="eyebrow">control</p>
            <h2>Session controls</h2>
          </div>

          <label className="field">
            <span>Microphone</span>
            <select
              value={selectedDeviceId}
              onChange={(event) => setSelectedDeviceId(event.target.value)}
              disabled={status.service_state === "running" || status.service_state === "starting"}
            >
              {devices.map((device) => (
                <option key={device.id} value={device.id}>
                  {device.name}
                  {device.is_default ? " (default)" : ""}
                </option>
              ))}
            </select>
          </label>

          <div className="button-row">
            <button
              onClick={handleStart}
              disabled={status.service_state === "running" || status.service_state === "starting"}
            >
              Start transcription
            </button>
            <button
              className="ghost"
              onClick={handleStop}
              disabled={status.service_state === "idle" || status.service_state === "stopping"}
            >
              Stop
            </button>
          </div>

          {status.mic_state === "permission_denied" && (
            <div className="alert danger">{status.last_error}</div>
          )}
          {actionError && <div className="alert danger">{actionError}</div>}

          <div className="status-grid">
            <div>
              <span className="label">Mic state</span>
              <strong>{status.mic_state}</strong>
            </div>
            <div>
              <span className="label">Display state</span>
              <strong>{status.display_state}</strong>
            </div>
            <div>
              <span className="label">Model</span>
              <strong>{status.engine_model || "Not started"}</strong>
            </div>
            <div>
              <span className="label">OLED preview</span>
              <strong>{status.current_oled_text || "No text yet"}</strong>
            </div>
          </div>

          {status.last_display_error && <div className="alert warn">{status.last_display_error}</div>}
        </article>

        <article className="panel transcript-panel">
          <div className="panel-head">
            <p className="eyebrow">live</p>
            <h2>Current phrase</h2>
          </div>
          <div className="live-card">
            <span className="label">Live partial</span>
            <p>{status.current_partial || "Waiting for speech..."}</p>
          </div>
          <div className="live-card secondary">
            <span className="label">OLED tail</span>
            <p>{status.current_oled_text || "No OLED text yet."}</p>
          </div>
        </article>
      </section>

      <section className="grid lower-grid">
        <article className="panel">
          <div className="panel-head">
            <p className="eyebrow">history</p>
            <h2>Recent sessions</h2>
          </div>
          <div className="session-list">
            {sessions.map((session) => (
              <button
                key={session.id}
                className={`session-item ${effectiveSessionId === session.id ? "active" : ""}`}
                onClick={() => setSelectedSessionId(session.id)}
              >
                <span>{formatDateTime(session.started_at_utc)}</span>
                <strong>{session.status}</strong>
                <small>{session.device_name || "Default microphone"}</small>
              </button>
            ))}
          </div>
        </article>

        <article className="panel">
          <div className="panel-head">
            <p className="eyebrow">segments</p>
            <h2>{selectedDetail ? "Finalized transcript" : "Select a session"}</h2>
          </div>
          <div className="segment-list">
            {liveSegments.length === 0 && <p className="empty">No finalized transcript yet.</p>}
            {liveSegments.map((segment) => (
              <article key={segment.id} className="segment-card">
                <div className="segment-meta">
                  <span>#{segment.sequence_no}</span>
                  <span>{formatDateTime(segment.ended_at_utc)}</span>
                </div>
                <p>{segment.text}</p>
                <small>OLED: {segment.oled_text}</small>
              </article>
            ))}
          </div>
        </article>
      </section>
    </main>
  );
}
