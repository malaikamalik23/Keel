import { useRef, useState } from "react";
import { api } from "../api";
import { useAction, useLoad } from "../hooks";
import type { DocumentRow } from "../types";
import { Badge, Card, Empty, ErrorNote, Loading } from "../ui";

type Link = { id: number | string; to: string; title: string | null; note: string | null };

const ACCEPT = ".pdf,.docx,.md,.markdown,.txt,.rst,.tex,.org";
const MAX_BYTES = 2_000_000;

function pagesLabel(d: DocumentRow) {
  if (!d.pages) return "";
  return d.pages_estimated ? `~${d.pages} pages (estimated)` : `${d.pages} pages`;
}

export default function Documents() {
  const { data, error, loading, reload } = useLoad<DocumentRow[]>("/api/documents");
  const links = useLoad<Record<string, Link[]>>("/api/document-links");
  const { run, error: actionError } = useAction(async () => { await reload(); await links.reload(); });
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
        <p className="small muted">Ask Keel in Chat to link a document to an application, task, project or email ("link my transcript to the DAAD application"), to move or rename it, or to edit a note. PDF, Word (.docx) or text, up to 2 MB. It is saved in your notes folder under uploads/ and indexed, so Keel can summarise it, cite its pages, compare it with another version and find its deadlines and action items.</p>
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
                <span>{d.path} <span className="muted small">{pagesLabel(d)} · {Math.max(1, Math.round(d.size / 1000))} KB</span>
                  {(links.data?.[d.path] ?? []).map((l) => (
                    <span key={l.id} className="doc-link">
                      {" "}<Badge>{l.to}</Badge> {l.title ?? ""}{l.note ? ` (${l.note})` : ""}
                      <button className="ghost small" aria-label={`Unlink ${d.path} from ${l.title ?? l.to}`}
                        onClick={() => run(() => api.del(`/api/document-links/${l.id}`))}>×</button>
                    </span>
                  ))}
                </span>
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
