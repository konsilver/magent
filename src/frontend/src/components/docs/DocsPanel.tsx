import { useRef } from 'react';
import { useUIStore } from '../../stores';
import type { UpdateFilter } from '../../stores/uiStore';

function showScrollbar(ref: React.RefObject<HTMLDivElement | null>) {
  return () => {
    if (ref.current) ref.current.classList.add('show-scrollbar');
  };
}

function hideScrollbar(ref: React.RefObject<HTMLDivElement | null>) {
  return () => {
    if (ref.current) ref.current.classList.remove('show-scrollbar');
  };
}

export default function DocsPanel() {
  const {
    activeUpdateFilter,
    setActiveUpdateFilter,
    featureUpdates,
  } = useUIStore();

  const updatesViewRef = useRef<HTMLDivElement>(null);

  const filteredUpdates = activeUpdateFilter === '全部'
    ? featureUpdates
    : featureUpdates.filter((e) => e.category === activeUpdateFilter);

  return (
    <div className="jx-docsNew">
      <div className="jx-updatesView" ref={updatesViewRef}
        onMouseEnter={showScrollbar(updatesViewRef)}
        onMouseLeave={hideScrollbar(updatesViewRef)}
      >
        <div className="jx-updateFilters">
          {(['全部', '模型迭代', '信息处理', '应用上新', '体验优化'] as UpdateFilter[]).map((f) => (
            <button
              key={f}
              className={`jx-updateFilterBtn${activeUpdateFilter === f ? ' active' : ''}`}
              onClick={() => setActiveUpdateFilter(f)}
            >
              {f}
            </button>
          ))}
        </div>
        <div className="jx-timeline">
          {filteredUpdates.map((entry, i) => (
            <div key={i} className="jx-tlItem">
              <div className="jx-tlDate">
                <span className="jx-tlDateMain">{entry.date}</span>
                <span className="jx-tlDateYear">{entry.year}</span>
              </div>
              <div className="jx-tlTrack">
                <div className="jx-tlDot" />
                {i < filteredUpdates.length - 1 && <div className="jx-tlLine" />}
              </div>
              <div className="jx-tlContent">
                <div className="jx-tlTitleRow">
                  <span className="jx-tlTitle">{entry.title}</span>
                  <span className={`jx-tlCatTag jx-cat-${entry.category}`}>{entry.category}</span>
                </div>
                <p className="jx-tlDesc">{entry.desc}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
