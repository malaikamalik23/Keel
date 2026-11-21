import { useRef, useState } from "react";
import { api } from "../api";
import { useAction, useLoad } from "../hooks";
import type { DocumentRow } from "../types";
import { Card, Empty, ErrorNote, Loading } from "../ui";

const ACCEPT = ".pdf,.docx,.md,.markdown,.txt,.rst,.tex,.org";
const MAX_BYTES = 2_000_000;

function pagesLabel(d: DocumentRow) {
  if (!d.pages) return "";
  return d.pages_estimated ? `~${d.pages} pages (estimated)` : `${d.pages} pages`;
}

export default function Documents() {
  const { data, error, loading, reload } = useLoad<DocumentRow[]>("/api/documents");
  const { run, error: actionError } = useAction(reload);
  const [note, setNote] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);

  const upload = (file: File | undefined) => {
    if (!file) return;
    setNote(null);
    if (file.size > MAX_BYTES) { setNote("That file is larger than 2 MB."); return; }
    void run(async () => {
      const doc = await api.upload<DocumentRow>(`/api/documents/upload?name=${encodeURIComponent(file.name)}`, file);
      setNote(`Added ${doc.path}${doc.hidden_skipped ? ` (${doc.hidden_skipped} piece(s) of hidden text left out)` : ""}. Ask Keel about it in Chat.`);
      if (input.current) input.current.value = "";
    });
  };

  return (
    <div>
      <Card title="Add a document">
        <p className="small muted">PDF, Word (.docx) or text, up to 2 MB. It is saved in your notes folder under uploads/ and indexed, so Keel can summarise it, cite its pages, compare it with another version and find its deadlines and action items.</p>
        <div className="toolbar">
          <input ref={input} type="file" accept={ACCEPT} aria-label="Document file" onChange={(e) => upload(e.target.files?.[0])} />
          <button className="ghost" onClick={() => run(() => api.post("/api/documents/index"))}>Re-scan notes folder</button>
        </div>
        {note && <p className="small" role="status">{note}</p>}
        <ErrorNote error={actionError} />
      </Card>
      <Loading loading={loading} error={error} />
      {data?.length === 0 && <Empty>No documents yet.</Empty>}
      {data && data.length > 0 && (
        <Card title={`Documents (${data.length})`}>
          <ul className="list">
            {data.map((d) => (
              <li key={d.id}>
                <span>{d.path} <span className="muted small">{pagesLabel(d)} · {Math.max(1, Math.round(d.size / 1000))} KB</span></span>
                {d.path.startsWith("uploads/") && (
                  <button className="ghost danger" onClick={() => window.confirm(`Delete ${d.path}? The file is removed from your notes folder.`) && run(() => api.del(`/api/documents/${d.id}`))}>Delete</button>
                )}
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
