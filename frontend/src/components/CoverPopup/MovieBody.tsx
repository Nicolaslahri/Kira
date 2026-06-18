import type { LibraryItem } from '../../lib/types';
import { IcAlertTri, IcCheck, IcX } from '../../lib/icons';
import { confTier } from '../LibraryGrid';
import { inferQuality, inferSource } from './quality';
import { MarqueeText } from './MarqueeText';

// Movie popup body — single-file layout adapted to the unified-row visual
// language: one rich pairing card (FILM badge + filename + tech chips +
// one confidence pill + status) followed by cast and the rename preview.
export function MovieBody({ item }: { item: LibraryItem }) {
  const file = item.files[0];
  if (!file) return null;
  const conf = file.confidence;
  const confT = confTier(conf);
  const wrong = file.matchedWrong;
  const tint = item.poster.tint;
  const statusClass =
    file.status === 'approved' ? 'approved' :
    file.status === 'rejected' ? 'rejected' :
    file.status === 'renamed' ? 'renamed' : '';

  return (
    <div className="cx-movie">
      <section className="cx-movie-section">
        <div className="cx-movie-section-label">The file</div>
        <div className={`cx-pair cx-pair-card ${statusClass} ${wrong ? 'wrong' : ''}`}>
          <div
            className="cx-pair-thumb ep film-thumb"
            style={{ ['--ep-a' as never]: tint[0], ['--ep-b' as never]: tint[1] } as React.CSSProperties}
          >
            <span className="ep-prefix">FILM</span>
            <span className="ep-num">●</span>
          </div>
          <div className="cx-pair-body">
            <div className="cx-pair-head">
              <div className="cx-pair-eptitle">
                {item.title}{item.year ? <span className="cx-pair-dur">· {item.year}</span> : null}
              </div>
            </div>
            <div className="cx-pair-file">
              <MarqueeText className="cx-pair-filename mono">
                <span title={file.filename}>{file.filename}</span>
              </MarqueeText>
              <MarqueeText className="cx-pair-folder mono">
                <span className="seg" title={file.folder}>{file.folder}</span>
              </MarqueeText>
              <div className="cx-pair-tags">
                {file.size ? <span className="cx-row-tag">{file.size}</span> : null}
                {(() => { const q = inferQuality(file) || file.quality; return q ? <span className="cx-row-tag">{q}</span> : null; })()}
                {(() => { const s = inferSource(file); return s ? <span className="cx-row-tag">{s}</span> : null; })()}
                {file.codec ? <span className="cx-row-tag">{file.codec}</span> : null}
                {file.hdr ? <span className="cx-row-tag hdr">{file.hdr}</span> : null}
                {file.releaseGroup ? <span className="cx-row-tag rg" title={file.releaseGroup}>[{file.releaseGroup}]</span> : null}
              </div>
              {wrong ? (
                <div className="cx-pair-wrong">
                  <span className="cx-row-warn"><IcAlertTri /> Wrong match</span>
                </div>
              ) : null}
            </div>
          </div>
          <div className="cx-pair-aside">
            {file.status === 'renamed' ? (
              <span className="cx-row-status renamed"><IcCheck /> Renamed</span>
            ) : file.status === 'approved' ? (
              <span className="cx-row-status approved"><IcCheck /> Approved</span>
            ) : file.status === 'rejected' ? (
              <span className="cx-row-status rejected"><IcX /> Rejected</span>
            ) : null}
            <span className={`cx-row-conf ${confT}`}>{conf}%</span>
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

      {/* "Will rename to" preview intentionally omitted: a movie's TRUE target
          depends on the library root + naming profile + file op, none of which
          this presentational card has. The Rename-preview modal shows the real
          dry-run target — do NOT reintroduce a hardcoded path here (the old one
          lied: fixed "/media/library/Movies/…", wrong profile, fabricated quality). */}
    </div>
  );
}
