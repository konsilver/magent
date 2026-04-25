import { useEffect, useMemo, useRef, useState } from 'react';
import { useAgentStore } from '../../stores/agentStore';

interface AgentMentionPopupProps {
  input: string;
  visible: boolean;
  selectedIndex: number;
  onSelect: (agentName: string) => void;
  onHover: (index: number) => void;
}

export function AgentMentionPopup({ input, visible, selectedIndex, onSelect, onHover }: AgentMentionPopupProps) {
  const { agents, fetchAgents } = useAgentStore();
  const itemRefs = useRef<(HTMLDivElement | null)[]>([]);

  useEffect(() => {
    if (visible && agents.length === 0) fetchAgents();
  }, [visible]);

  const mentionQuery = useMemo(() => {
    if (!visible) return '';
    const lastAt = input.lastIndexOf('@');
    if (lastAt === -1) return '';
    return input.slice(lastAt + 1).toLowerCase();
  }, [input, visible]);

  const filtered = useMemo(() => {
    const list = agents.filter((a) => a.is_enabled);
    if (!mentionQuery) return list;
    return list.filter((a) => a.name.toLowerCase().includes(mentionQuery));
  }, [agents, mentionQuery]);

  useEffect(() => {
    if (visible && itemRefs.current[selectedIndex]) {
      itemRefs.current[selectedIndex]!.scrollIntoView({ block: 'nearest' });
    }
  }, [selectedIndex, visible]);

  if (!visible || filtered.length === 0) return null;

  return (
    <div className="jx-mentionPopup" onMouseDown={(e) => e.preventDefault()}>
      {filtered.map((agent, idx) => (
        <div
          key={agent.agent_id}
          ref={(el) => { itemRefs.current[idx] = el; }}
          className={`jx-mentionPopup-item${idx === selectedIndex ? ' active' : ''}`}
          onMouseEnter={() => onHover(idx)}
          onClick={() => onSelect(agent.name)}
        >
          <span className="jx-mentionPopup-at">@</span>
          <span className="jx-mentionPopup-name">{agent.name}</span>
        </div>
      ))}
    </div>
  );
}

/**
 * Hook: @mention popup visibility + keyboard nav.
 */
export function useAgentMention() {
  const { agents } = useAgentStore();
  const [mentionVisible, setMentionVisible] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState(0);

  function getFiltered(input: string) {
    const lastAt = input.lastIndexOf('@');
    const query = lastAt === -1 ? '' : input.slice(lastAt + 1).toLowerCase();
    const list = agents.filter((a) => a.is_enabled);
    if (!query) return list;
    return list.filter((a) => a.name.toLowerCase().includes(query));
  }

  function handleInputChange(value: string, prevValue: string) {
    // Count @ signs — works regardless of where the @ was inserted
    const newAtCount = (value.match(/@/g) || []).length;
    const oldAtCount = (prevValue.match(/@/g) || []).length;
    if (newAtCount > oldAtCount) {
      setMentionVisible(true);
      setSelectedIndex(0);
      return;
    }
    if (mentionVisible) {
      const lastAt = value.lastIndexOf('@');
      if (lastAt === -1) {
        setMentionVisible(false);
      } else {
        const afterAt = value.slice(lastAt + 1);
        if (afterAt.includes(' ')) setMentionVisible(false);
      }
    }
  }

  /** Only handles ArrowUp/Down. Enter/Tab handled by InputArea. */
  function handleKeyDown(e: React.KeyboardEvent, input: string) {
    if (!mentionVisible) return;
    const filtered = getFiltered(input);
    if (filtered.length === 0) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSelectedIndex((i) => Math.min(i + 1, filtered.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSelectedIndex((i) => Math.max(i - 1, 0));
    }
  }

  return {
    mentionVisible, setMentionVisible,
    selectedIndex, setSelectedIndex,
    handleInputChange, handleKeyDown, getFiltered,
  };
}
