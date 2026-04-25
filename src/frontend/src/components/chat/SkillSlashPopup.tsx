import { useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import { useCatalogStore } from '../../stores';

interface SkillSlashPopupProps {
  input: string;
  visible: boolean;
  selectedIndex: number;
  onSelect: (skillId: string, skillName: string) => void;
  onHover: (index: number) => void;
}

export function SkillSlashPopup({ input, visible, selectedIndex, onSelect, onHover }: SkillSlashPopupProps) {
  const { catalog } = useCatalogStore();
  const itemRefs = useRef<(HTMLDivElement | null)[]>([]);

  const slashQuery = useMemo(() => {
    if (!visible) return '';
    if (!input.startsWith('/')) return '';
    return input.slice(1).toLowerCase();
  }, [input, visible]);

  const filtered = useMemo(() => {
    const enabledSkills = (catalog.skills || []).filter((s) => s.enabled);
    if (!slashQuery) return enabledSkills;
    return enabledSkills.filter((s) => s.name.toLowerCase().includes(slashQuery));
  }, [catalog.skills, slashQuery]);

  useEffect(() => {
    if (visible && itemRefs.current[selectedIndex]) {
      itemRefs.current[selectedIndex]!.scrollIntoView({ block: 'nearest' });
    }
  }, [selectedIndex, visible]);

  const showPopup = visible && filtered.length > 0;

  return (
    <AnimatePresence>
      {showPopup && (
        <motion.div
          className="jx-slashPopup"
          onMouseDown={(e) => e.preventDefault()}
          initial={{ opacity: 0, y: 6, scale: 0.97 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: 4, scale: 0.97 }}
          transition={{ duration: 0.16, ease: 'easeOut' }}
        >
          {filtered.map((skill, idx) => (
            <div
              key={skill.id}
              ref={(el) => { itemRefs.current[idx] = el; }}
              className={`jx-slashPopup-item${idx === selectedIndex ? ' active' : ''}`}
              onMouseEnter={() => onHover(idx)}
              onClick={() => onSelect(skill.id, skill.name)}
            >
              <span className="jx-slashPopup-name">{skill.name}</span>
            </div>
          ))}
        </motion.div>
      )}
    </AnimatePresence>
  );
}

/**
 * Hook: / slash command popup visibility + keyboard nav.
 */
export function useSkillSlash() {
  const [slashVisible, setSlashVisible] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState(0);

  function getFiltered(input: string) {
    const { catalog } = useCatalogStore.getState();
    const enabledSkills = (catalog.skills || []).filter((s) => s.enabled);
    if (!input.startsWith('/')) return enabledSkills;
    const query = input.slice(1).toLowerCase();
    if (!query) return enabledSkills;
    return enabledSkills.filter((s) => s.name.toLowerCase().includes(query));
  }

  function handleSlashInputChange(value: string, prevValue: string) {
    const v = value.trimEnd();   // contentEditable may append \n
    const p = prevValue.trimEnd();
    if (p === '' && v === '/') {
      setSlashVisible(true);
      setSelectedIndex(0);
      return;
    }
    if (slashVisible) {
      if (v.startsWith('/') && !v.slice(1).includes(' ')) {
        setSelectedIndex(0);
      } else {
        setSlashVisible(false);
      }
    }
  }

  /** Only handles ArrowUp/Down/Escape. Enter/Tab handled by InputArea. */
  function handleSlashKeyDown(e: React.KeyboardEvent): boolean {
    if (!slashVisible) return false;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSelectedIndex((i) => i + 1); // clamped by popup render
      return true;
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSelectedIndex((i) => Math.max(i - 1, 0));
      return true;
    }
    if (e.key === 'Escape') {
      e.preventDefault();
      setSlashVisible(false);
      return true;
    }
    return false;
  }

  return {
    slashVisible, setSlashVisible,
    selectedIndex, setSelectedIndex,
    handleSlashInputChange, handleSlashKeyDown, getFiltered,
  };
}
