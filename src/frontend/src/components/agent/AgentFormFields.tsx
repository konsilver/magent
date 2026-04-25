import { Form, Input, Select, InputNumber, Spin, Switch } from 'antd';
import type { AvailableResources } from '../../stores/agentStore';

const { TextArea } = Input;

interface AgentFormFieldsProps {
  availableResources: AvailableResources | null;
}

export function AgentFormFields({ availableResources }: AgentFormFieldsProps) {
  const mcpOptions = (availableResources?.mcp_servers || []).map((s) => ({
    label: s.name,
    value: s.id,
  }));
  const skillOptions = (availableResources?.skills || []).map((s) => ({
    label: s.name,
    value: s.id,
  }));

  return (
    <>
      <Form.Item
        name="name"
        label="名称"
        rules={[{ required: true, message: '请输入智能体名称' }]}
      >
        <Input placeholder="如：产业链分析师" maxLength={50} />
      </Form.Item>

      <Form.Item name="description" label="简介">
        <Input
          placeholder="一句话描述智能体的用途，限 20 字"
          maxLength={20}
          showCount
        />
      </Form.Item>

      <Form.Item
        name="system_prompt"
        label="角色设定 (System Prompt)"
        rules={[{ required: true, message: '请输入角色设定' }]}
      >
        <TextArea
          rows={5}
          placeholder="定义智能体的角色、专长和行为规范..."
          maxLength={5000}
          showCount
        />
      </Form.Item>

      <Form.Item name="welcome_message" label="开场白">
        <TextArea
          rows={2}
          placeholder="用户打开对话时的欢迎消息"
          maxLength={500}
        />
      </Form.Item>

      <Form.Item name="mcp_server_ids" label="绑定工具 (MCP)">
        {availableResources ? (
          <Select
            mode="multiple"
            placeholder="选择可用的 MCP 工具"
            options={mcpOptions}
            allowClear
          />
        ) : (
          <Spin size="small" />
        )}
      </Form.Item>

      <Form.Item name="skill_ids" label="绑定技能">
        {availableResources ? (
          <Select
            mode="multiple"
            placeholder="选择可用的技能"
            options={skillOptions}
            allowClear
          />
        ) : (
          <Spin size="small" />
        )}
      </Form.Item>

      <Form.Item name="max_iters" label="最大推理轮次">
        <InputNumber min={1} max={30} style={{ width: '100%' }} />
      </Form.Item>

      <Form.Item
        name="temperature"
        label="温度 (Temperature)"
        tooltip="控制生成结果的随机性；值越低越确定，越高越发散。范围 0–2，默认 0.6"
      >
        <InputNumber
          min={0}
          max={2}
          step={0.1}
          placeholder="0.6"
          style={{ width: '100%' }}
        />
      </Form.Item>

      <Form.Item
        name="shared_context"
        label="共享上下文"
        valuePropName="checked"
        tooltip="启用后，被主智能体调用时可读取完整对话历史和工具调用结果"
      >
        <Switch />
      </Form.Item>
    </>
  );
}
