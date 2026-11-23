import { useRef, useState } from "react";
import { api } from "../api";
import { useAction, useLoad } from "../hooks";
import { Icon } from "../icons";
import { useToast } from "../toast";
import type { DocumentRow } from "../types";
import { Badge, Card, Empty, ErrorNote, Loading } from "../ui";

type Link = { id: number | string; to: string; title: string | null; note: string | null };

const ACCEPT = ".pdf,.docx,.md,.markdown,.txt,.rst,.tex,.org";
const MAX_BYTES = 2_000_000;

function pagesLabel(d: DocumentRow) {
  if (!d.pages) return "";
  return d.pages_estimated ? `~${d.pages} pages (estimated)` : `${d.pages} pages`;
}

const ext = (path: string) => (path.includes(".") ? path.split(".").pop()!.toLowerCase() : "file");
const EXT_TONE: Record<string, string> = { pdf: "bad", docx: "info", md: "ok", txt: "" };

export default function Documents({ ask }: { ask?: (text: string) => void }) {
  const { data, error, loading, reload } = useLoad<DocumentRow[]>("/api/documents");
  const links = useLoad<Record<string, Link[]>>("/api/document-links");
  const { run, error: actionError } = useAction(async () => { await reload(); await links.reload(); });
  const toast = useToast();
  const [note, setNote] = useState<string | null>(null);
  const [over, setOver] = useState(false);
  const [q, setQ] = useState("");
  const input = useRef<HTMLInputElement>(null);

  const upload = (file: File | undefined) => {
    if (!file) return;
    setNote(null);
    if (file.size > MAX_BYTES) { setNote("That file is larger than 2 MB."); return; }
    void run(async () => {
      const doc = await api.upload<DocumentRow>(`/api/documents/upload?name=${encodeURIComponent(file.name)}`, file);
      setNote(`Added ${doc.path}${doc.hidden_skipped ? ` (${doc.hidden_skipped} piece(s) of hidden text left out)` : ""}. Ask Keel about it in Chat.`);
      toast(`Added ${doc.path}`);
      if (input.current) input.current.value = "";
    });
  };

  const needle = q.trim().toLowerCase();
  const shown = (data ?? []).filter((d) => !needle || d.path.toLowerCase().includes(needle));

  return (
    <div>
      <Card title="Add a document">
        <div className={`dropzone${over ? " over" : ""}`}
          onDragOver={(e) => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); setOver(true); } }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => { e.preventDefault(); setOver(false); upload(e.dataTransfer.files?.[0]); }}>
          <Icon name="document" size={28} />
          <p><strong>Drop a file here</strong> or choose one</p>
          <p className="small muted">PDF, Word (.docx) or text, up to 2 MB</p>
          <div className="toolbar">
            <input ref={input} type="file" accept={ACCEPT} aria-label="Document file" onChange={(e) => upload(e.target.files?.[0])} />
            <button className="ghost small" onClick={() => run(async () => { await api.post("/api/documents/index"); toast("Notes folder re-scanned"); })}>Re-scan notes folder</button>
          </div>
        </div>
        {note && <p className="small" role="status">{note}</p>}
        <p className="small muted">It is saved in your notes folder under uploads/ and indexed, so Keel can summarise it, cite its pages, compare it with another version and find its deadlines and action items. Ask Keel in Chat to link a document to an application, task, project or email ("link my transcript to the DAAD application").</p>
        <ErrorNote error={actionError} />
      </Card>
      <Loading loading={loading} error={error} />
      {data?.length === 0 && <Empty>No documents yet.</Empty>}
      {data && data.length > 0 && (
        <Card title={`Documents (${data.length})`} right={
          <label className="search"><Icon name="search" size={15} /><input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find a document…" aria-label="Find a document" /></label>
        }>
          {shown.length === 0 && <Empty>Nothing matches “{q}”.</Empty>}
          <ul className="list doc-list">
            {shown.map((d) => (
              <li key={d.id}>
                <span className="doc-main">
                  <span className={`doc-ext ${EXT_TONE[ext(d.path)] ?? ""}`} aria-hidden="true">{ext(d.path).slice(0, 4)}</span>
                  <span>
                    <span className="doc-name">{d.path}</span>
                    <span className="muted small"> {pagesLabel(d)} · {Math.max(1, Math.round(d.size / 1000))} KB</span>
                    {(links.data?.[d.path] ?? []).map((l) => (
                      <span key={l.id} className="doc-link">
                        {" "}<Badge>{l.to}</Badge> {l.title ?? ""}{l.note ? ` (${l.note})` : ""}
                        <button className="ghost small" aria-label={`Unlink ${d.path} from ${l.title ?? l.to}`}
                          onClick={() => run(async () => { await api.del(`/api/document-links/${l.id}`); toast(`Unlinked from ${l.title ?? l.to}`, { tone: "info" }); })}>×</button>
                      </span>
                    ))}
                  </span>
                </span>
                <span className="actions">
                  {ask && <button className="ghost small" aria-label={`Ask Keel about ${d.path}`} onClick={() => ask(`Summarise ${d.path} and list any deadlines and action items in it`)}><Icon name="sparkle" size={14} /> Summarise</button>}
                  {d.path.startsWith("uploads/") && (
                    <button className="ghost danger small" onClick={() => window.confirm(`Delete ${d.path}? The file is removed from your notes folder.`) && run(async () => { await api.del(`/api/documents/${d.id}`); toast(`Deleted ${d.path}`, { tone: "info" }); })}>Delete</button>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
