import { useEffect, useState } from 'react';
import { Button, Form, message } from 'antd';
import { ArrowLeftOutlined, PlusOutlined, EditOutlined } from '@ant-design/icons';
import { useAgentStore, type UserAgentItem } from '../../stores/agentStore';
import { AgentFormFields } from './AgentFormFields';
import { getRandomIconUrl } from './AgentPanel';

interface AgentCreatePageProps {
  onBack: () => void;
  onCreated: () => void;
  agent?: UserAgentItem | null; // null/undefined = create mode, provided = edit mode
}

export function AgentCreatePage({ onBack, onCreated, agent }: AgentCreatePageProps) {
  const { createAgent, updateAgent, fetchAgents, fetchAvailableResources, availableResources } = useAgentStore();
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);
  const isEdit = !!agent;
  const [heroIconUrl] = useState(() =>
    isEdit && agent ? getRandomIconUrl(agent.agent_id || agent.name) : getRandomIconUrl(String(Date.now()))
  );

  useEffect(() => {
    fetchAvailableResources();
    if (agent) {
      const ec = agent.extra_config || {};
      form.setFieldsValue({
        name: agent.name,
        description: agent.description,
        system_prompt: agent.system_prompt,
        welcome_message: agent.welcome_message,
        mcp_server_ids: agent.mcp_server_ids || [],
        skill_ids: agent.skill_ids || [],
        max_iters: agent.max_iters ?? 10,
        shared_context: !!ec.shared_context,
      });
    } else {
      form.resetFields();
      form.setFieldsValue({ max_iters: 10, shared_context: false });
    }
  }, [agent]);

  async function handleSubmit() {
    try {
      const values = await form.validateFields();
      // 将 shared_context 合并到 extra_config
      const { shared_context, ...rest } = values;
      const existingExtra = (isEdit ? agent?.extra_config : {}) || {};
      const extra_config: Record<string, unknown> = {
        ...existingExtra,
        shared_context: !!shared_context,
      };
      const payload = { ...rest, extra_config };

      setSaving(true);
      if (isEdit) {
        await updateAgent(agent!.agent_id, payload);
        message.success('已更新');
      } else {
        await createAgent(payload);
        message.success('已创建');
      }
      await fetchAgents();
      onCreated();
    } catch (e: any) {
      if (e.errorFields) return;
      message.error(e.message || (isEdit ? '更新失败' : '创建失败'));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="jx-agentCreatePage">
      <div className="jx-agentCreatePage-head">
        <button type="button" className="jx-agentCreatePage-back" onClick={onBack}>
          <ArrowLeftOutlined />
          <span>返回子智能体列表</span>
        </button>
        <div className="jx-agentCreatePage-hero">
          <div className="jx-agentCreatePage-heroIcon">
            <img src={heroIconUrl} alt="" width={32} height={32} style={{ display: 'block' }} />
          </div>
          <div className="jx-agentCreatePage-heroBody">
            <h2 className="jx-agentCreatePage-title">{isEdit ? '编辑智能体' : '创建智能体'}</h2>
            <p className="jx-agentCreatePage-subtitle">配置智能体名称、角色设定、绑定工具与技能</p>
          </div>
        </div>
      </div>

      <div className="jx-agentCreatePage-card">
        <Form form={form} layout="vertical">
          <AgentFormFields availableResources={availableResources} />
        </Form>

        <div className="jx-agentCreatePage-actions">
          <Button onClick={onBack}>取消</Button>
          <Button
            type="primary"
            icon={isEdit ? <EditOutlined /> : <PlusOutlined />}
            loading={saving}
            onClick={() => void handleSubmit()}
          >
            {isEdit ? '保存更改' : '创建智能体'}
          </Button>
        </div>
      </div>
    </div>
  );
}
