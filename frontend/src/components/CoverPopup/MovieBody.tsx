import type { LibraryItem } from '../../lib/types';
import { IcAlertTri } from '../../lib/icons';
import { confTier } from '../LibraryGrid';

// Movie popup body — single-file layout (no synced episode columns).
// Shows the matched file, cast, and the rename target preview.
export function MovieBody({ item }: { item: LibraryItem }) {
  const file = item.files[0];
  if (!file) return null;
  const conf = file.confidence;
  const confT = confTier(conf);
  const wrong = file.matchedWrong;
  const ext = file.filename.split('.').pop() || 'mkv';

  return (
    <div className="cx-body single">
      <div className="cx-movie">
        <section className="cx-movie-section">
          <div className="cx-movie-section-label">Your file</div>
          <div className={`cx-row cx-row-static ${file.status === 'approved' ? 'approved' : ''} ${file.status === 'rejected' ? 'rejected' : ''} ${wrong ? 'wrong' : ''}`}>
            <div className="cx-file-row">
              <div className="cx-pair-thumb file detected">
                <span className="ep-prefix">FILM</span>
                <span className="ep-num">●</span>
              </div>
              <div className="cx-row-content">
                <div className="cx-row-title mono">{file.filename}</div>
                <div className="cx-row-sub mono"><span className="seg">{file.folder}</span></div>
                <div className="cx-row-tags">
                  {file.size ? <span className="cx-row-tag">{file.size}</span> : null}
                  {file.quality ? <span className="cx-row-tag">{file.quality}</span> : null}
                  {wrong ? <span className="cx-row-warn"><IcAlertTri /> Wrong match</span> : null}
                </div>
              </div>
              <div className="cx-row-aside">
                <span className={`cx-row-conf ${confT}`}>{conf}%</span>
                <span className="cx-movie-status">
                  {file.status === 'approved'
                    ? <span style={{ color: 'var(--conf-high)' }}>✓ Approved</span>
                    : file.status === 'rejected'
                      ? <span style={{ color: 'var(--conf-low)' }}>✕ Rejected</span>
                      : <span style={{ color: 'var(--ink-3)' }}>Pending</span>}
                </span>
              </div>
            </div>
          </div>
        </section>

        {item.cast?.length ? (
          <section className="cx-movie-section">
            <div className="cx-movie-section-label">Cast</div>
            <div className="cx-cast-list">
              {item.cast.map((c, i) => <span key={i} className="cx-cast-chip">{c}</span>)}
            </div>
          </section>
        ) : null}

        <section className="cx-movie-section">
          <div className="cx-movie-section-label">Will rename to</div>
          <div className="cx-rename-target">
            <span className="seg-dir">/media/library/Movies/{item.title} ({item.year})/</span>
            <span className="seg-new">{item.title} ({item.year}) [{file.quality || '1080p'}].{ext}</span>
          </div>
        </section>
      </div>
    </div>
  );
}
