import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Form,
  Input,
  InputNumber,
  Button,
  Card,
  Space,
  Typography,
  Radio,
  Alert,
  Spin,
  message,
  Divider,
  Progress,
  Result,
} from 'antd';
import {
  ArrowLeftOutlined,
  SendOutlined,
  LoadingOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined
} from '@ant-design/icons';
import { feedbackService, recommendationService } from '../services';
import type {
  ExperimentResultRequest,
  RecommendationDetail,
  FeedbackStatusData
} from '../types';

const { Title, Paragraph, Text } = Typography;
const { TextArea } = Input;

function formatRatioNumber(value: number): string {
  return value.toFixed(4).replace(/\.?0+$/, '');
}

function buildSolidLiquidRatioText(
  solidMass?: number,
  liquidMass?: number
): string | undefined {
  if (
    solidMass === undefined ||
    solidMass === null ||
    liquidMass === undefined ||
    liquidMass === null ||
    solidMass <= 0 ||
    liquidMass < 0
  ) {
    return undefined;
  }

  return `1:${formatRatioNumber(liquidMass / solidMass)} g:g`;
}

function FeedbackPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [detail, setDetail] = useState<RecommendationDetail | null>(null);
  const [isLiquidFormed, setIsLiquidFormed] = useState<boolean>(true);
  const solidLiquidRatio = Form.useWatch(['conditions', 'solid_liquid_ratio'], form);

  // Processing status
  const [isProcessing, setIsProcessing] = useState(false);
  const [processingStatus, setProcessingStatus] = useState<FeedbackStatusData | null>(null);

  useEffect(() => {
    const solidMass = solidLiquidRatio?.solid_mass_g;
    const liquidMass = solidLiquidRatio?.liquid_volume_ml;
    const nextRatioText = buildSolidLiquidRatioText(solidMass, liquidMass);

    if (nextRatioText && solidLiquidRatio?.ratio_text !== nextRatioText) {
      form.setFieldValue(['conditions', 'solid_liquid_ratio', 'ratio_text'], nextRatioText);
    } else if (!nextRatioText && solidLiquidRatio?.ratio_text) {
      form.setFieldValue(['conditions', 'solid_liquid_ratio', 'ratio_text'], undefined);
    }
  }, [form, solidLiquidRatio]);

  useEffect(() => {
    if (!id) return;

    const fetchDetail = async () => {
      setLoading(true);
      try {
        const response = await recommendationService.getRecommendationDetail(id);
        setDetail(response.data);

        // If already has experiment result, pre-fill the form
        if (response.data.experiment_result) {
          const expResult = response.data.experiment_result;
          // Prefill conditions and measurements (long-table mode)
          const expConditions = expResult.conditions || {};
          const expRatio = expConditions.solid_liquid_ratio || {};
          const expMeasurements = expResult.measurements || [];

          form.setFieldsValue({
            is_liquid_formed: expResult.is_liquid_formed,
            conditions: {
              temperature_C: expConditions.temperature_C,
              solid_liquid_ratio: {
                solid_mass_g: expRatio.solid_mass_g,
                liquid_volume_ml: expRatio.liquid_volume_ml,
                ratio_text: expRatio.ratio_text,
              },
            },
            measurements: expMeasurements.length > 0 ? expMeasurements : undefined,
            notes: expResult.notes || '',
            // Convert properties object to text format
            properties_text: expResult.properties
              ? Object.entries(expResult.properties)
                  .map(([key, value]) => `${key}=${value}`)
                  .join('\n')
              : '',
            // Keep raw properties in sync so that updates don't silently wipe old values
            properties: expResult.properties || undefined,
          });
          setIsLiquidFormed(expResult.is_liquid_formed);
          message.info('已加载当前反馈数据，您可以修改后重新提交');
        }
      } catch (error) {
        console.error('Failed to fetch recommendation detail:', error);
      } finally {
        setLoading(false);
      }
    };

    fetchDetail();
  }, [id, form]);

  const handleSubmit = async (values: ExperimentResultRequest) => {
    if (!id) return;

    // Validation:
    // - If liquid formed: require at least one measurement row and at least one leaching_efficiency value
    // - If not formed: measurements can be empty; if provided, leaching_efficiency must be empty/0
    const measurements = values.measurements || [];

    if (values.is_liquid_formed) {
      if (measurements.length === 0) {
        message.error('液体形成时，请至少添加一条浸出效率测量记录');
        return;
      }
      // 如果液体形成，要求至少有一条带浸出效率的测量
      const hasSol = measurements.some((m) => m.leaching_efficiency !== undefined && m.leaching_efficiency !== null);
      if (!hasSol) {
        message.error('液体形成时，请在测量记录中提供至少一个浸出效率数值');
        return;
      }
    } else {
      // If not formed, allow empty measurements; but if provided, disallow positive efficiency values.
      const badIndex = measurements.findIndex(
        (m) => m.leaching_efficiency !== undefined && m.leaching_efficiency !== null && m.leaching_efficiency !== 0
      );
      if (badIndex !== -1) {
        form.setFields([
          {
            name: ['measurements', badIndex, 'leaching_efficiency'] as any,
            errors: ['液体未形成时，不应填写浸出效率（请清空或填写 0）'],
          },
        ]);
        message.error('液体未形成时，不应填写浸出效率（请清空或填写 0）');
        return;
      }
    }

    setSubmitting(true);
    try {
      // Submit feedback (async)
      await feedbackService.submitFeedback({
        recommendation_id: id,
        experiment_result: values,
      });

      message.success('反馈已提交，正在后台处理...');

      // Switch to processing mode
      setSubmitting(false);
      setIsProcessing(true);
      setProcessingStatus({
        status: 'processing',
        started_at: new Date().toISOString(),
      });

      // Start polling
      try {
        const finalStatus = await feedbackService.pollStatus(
          id,
          (statusResponse) => {
            // Update status during polling
            setProcessingStatus(statusResponse.data);
          },
          2000, // Poll every 2 seconds
          300000 // 5 minute timeout
        );

        // Processing completed
        setProcessingStatus(finalStatus.data);
        message.success({
          content: `反馈处理完成！提取了 ${finalStatus.data.result?.num_memories || 0} 条记忆`,
          duration: 5,
        });

      } catch (pollError: any) {
        console.error('Polling error:', pollError);
        message.error(pollError.message || '处理超时或失败');
        setProcessingStatus({
          status: 'failed',
          started_at: processingStatus?.started_at || new Date().toISOString(),
          failed_at: new Date().toISOString(),
          error: pollError.message || '处理失败',
        });
      }

    } catch (error: any) {
      console.error('Failed to submit feedback:', error);
      const data = error?.response?.data;
      const detail = data?.detail || {};
      const msg =
        detail.message ||
        data?.message ||
        error?.message ||
        '提交失败';

      // Optional: map backend field name to form paths
      const field = detail.field as string | undefined;
      const index = typeof detail.index === 'number' ? (detail.index as number) : undefined;

      if (field) {
        let namePath: (string | number)[] | undefined;

        if (field === 'measurements') {
          // 如果有 index，尽量高亮到该行；否则高亮整个列表
          namePath = index !== undefined ? ['measurements', index] : ['measurements'];
        } else if (field === 'time_h') {
          namePath = index !== undefined ? ['measurements', index, 'time_h'] : ['measurements'];
        } else if (field === 'leaching_efficiency') {
          namePath = index !== undefined ? ['measurements', index, 'leaching_efficiency'] : ['measurements'];
        } else if (field === 'unit') {
          namePath = index !== undefined ? ['measurements', index, 'unit'] : ['measurements'];
        } else if (field === 'recommendation_id') {
          namePath = ['recommendation_id'];
        }

        if (namePath) {
          form.setFields([
            {
              name: namePath as any,
              errors: [msg],
            },
          ]);
        }
      }

      message.error(msg);
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: '100px 0' }}>
        <Spin size="large" />
      </div>
    );
  }

  if (!detail) {
    return (
      <Alert
        message="推荐不存在"
        description="未找到该推荐信息"
        type="error"
        showIcon
      />
    );
  }

  if (detail.status !== 'PENDING' && detail.status !== 'PROCESSING' && detail.status !== 'COMPLETED') {
    return (
      <Alert
        message="无法提交反馈"
        description={`该推荐的状态为 ${detail.status}，只有待实验或已完成状态的推荐才能提交/更新反馈`}
        type="warning"
        showIcon
      />
    );
  }

  // Show processing status
  if (isProcessing && processingStatus) {
    return (
      <div>
        <Space style={{ marginBottom: 16 }}>
          <Button
            icon={<ArrowLeftOutlined />}
            onClick={() => navigate(`/recommendations/${id}`)}
            disabled={processingStatus.status === 'processing'}
          >
            返回详情
          </Button>
        </Space>

        <Card>
          {processingStatus.status === 'processing' && (
            <Result
              icon={<LoadingOutlined style={{ fontSize: 48, color: '#1890ff' }} />}
              title="正在处理反馈..."
              subTitle="系统正在提取实验记忆并更新知识库，这可能需要几秒钟"
              extra={
                <div style={{ textAlign: 'center' }}>
                  <Progress percent={100} status="active" showInfo={false} />
                  <Paragraph style={{ marginTop: 16 }}>
                    <Text type="secondary">
                      开始时间: {new Date(processingStatus.started_at).toLocaleString()}
                    </Text>
                  </Paragraph>
                </div>
              }
            />
          )}

          {processingStatus.status === 'completed' && processingStatus.result && (
            <Result
              status="success"
              icon={<CheckCircleOutlined style={{ fontSize: 48, color: '#52c41a' }} />}
              title="反馈处理完成！"
              subTitle={
                <div>
                  <Paragraph>
                    {processingStatus.result.is_liquid_formed
                      ? 'DES液体成功形成'
                      : 'DES液体未成功形成'}
                  </Paragraph>
                  <Paragraph>
                    已处理测量 <Text strong>{processingStatus.result.measurement_count ?? 0}</Text> 条，提取了 <Text strong>{processingStatus.result.num_memories}</Text> 条记忆
                  </Paragraph>
                  {processingStatus.is_update && processingStatus.deleted_memories !== undefined && processingStatus.deleted_memories > 0 && (
                    <Alert
                      type="warning"
                      message="更新操作"
                      description={`已删除 ${processingStatus.deleted_memories} 条旧记忆并更新为新的实验记忆`}
                      showIcon
                      style={{ marginTop: 8 }}
                    />
                  )}
                </div>
              }
              extra={[
                <Button
                  key="confirm"
                  type="primary"
                  onClick={() => navigate(`/recommendations/${id}`)}
                >
                  确定
                </Button>,
                <Button key="list" onClick={() => navigate('/recommendations')}>
                  返回列表
                </Button>,
              ]}
            />
          )}

          {processingStatus.status === 'failed' && (
            <Result
              status="error"
              icon={<CloseCircleOutlined style={{ fontSize: 48, color: '#ff4d4f' }} />}
              title="处理失败"
              subTitle={processingStatus.error || '反馈处理过程中发生错误'}
              extra={[
                <Button
                  key="retry"
                  type="primary"
                  onClick={() => {
                    setIsProcessing(false);
                    setProcessingStatus(null);
                  }}
                >
                  重新提交
                </Button>,
                <Button key="back" onClick={() => navigate(`/recommendations/${id}`)}>
                  返回详情
                </Button>,
              ]}
            />
          )}
        </Card>
      </div>
    );
  }

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Button
          icon={<ArrowLeftOutlined />}
          onClick={() => navigate(`/recommendations/${id}`)}
        >
          返回详情
        </Button>
      </Space>

      <Title level={2}>
        {detail.status === 'COMPLETED' ? '更新实验反馈' : '提交实验反馈'}
      </Title>
      <Paragraph>
        {detail.status === 'COMPLETED' ? (
          <>
            您正在更新已提交的反馈。系统将删除旧记忆并提取新的实验记忆。
            {detail.experiment_result && (
              <Alert
                type="info"
                message="当前反馈数据"
                description={
                  <div>
                    <div>液体形成：{detail.experiment_result.is_liquid_formed ? '是' : '否'}</div>
            {detail.experiment_result.measurements && detail.experiment_result.measurements.length > 0 && (
              <div>已提交测量：{detail.experiment_result.measurements.length} 条</div>
            )}
                  </div>
                }
                showIcon
                style={{ marginTop: 8 }}
              />
            )}
          </>
        ) : (
          '请填写您的实验结果，系统将自动学习并优化未来的推荐。'
        )}
      </Paragraph>

      <Card title="推荐配方信息" style={{ marginBottom: 24 }}>
        <Paragraph>
          <Text strong>配方:</Text> {detail.formulation.HBD} : {detail.formulation.HBA} ({detail.formulation.molar_ratio})
        </Paragraph>
        <Paragraph>
          <Text strong>目标材料:</Text> {detail.task.target_material}
        </Paragraph>
        <Paragraph>
          <Text strong>目标温度:</Text> {detail.task.target_temperature}°C
        </Paragraph>
      </Card>

      <Card title="实验结果">
        <Form
          form={form}
          layout="vertical"
          onFinish={handleSubmit}
          initialValues={{
            is_liquid_formed: true,
            conditions: { temperature_C: undefined, solid_liquid_ratio: { ratio_text: undefined } },
            measurements: [
              { target_material: detail.task?.target_material, time_h: 1, unit: '%' }
            ],
          }}
        >
          <Form.Item
            label="DES液体是否成功形成？"
            name="is_liquid_formed"
            rules={[{ required: true, message: '请选择液体是否形成' }]}
          >
            <Radio.Group onChange={(e) => setIsLiquidFormed(e.target.value)}>
              <Radio value={true}>是</Radio>
              <Radio value={false}>否</Radio>
            </Radio.Group>
          </Form.Item>

          {!isLiquidFormed && (
            <Alert
              message="液体未形成"
              description="如果DES液体未能成功形成，请在备注中说明原因（如固体未溶解、分层等）"
              type="warning"
              showIcon
              style={{ marginBottom: 16 }}
            />
          )}

          <Divider orientation="left">实验条件</Divider>
          <Form.Item label="实验温度 (°C)" name={['conditions', 'temperature_C']}>
            <InputNumber
              style={{ width: '100%' }}
              min={-80}
              max={200}
              step={0.1}
              placeholder="例如: 25"
            />
          </Form.Item>
          <Space size="small" align="start">
            <Form.Item label="固体质量 (g)" name={['conditions', 'solid_liquid_ratio', 'solid_mass_g']}>
              <InputNumber min={0} step={0.1} placeholder="例如 1.0" />
            </Form.Item>
            <Form.Item label="液体质量 (g)" name={['conditions', 'solid_liquid_ratio', 'liquid_volume_ml']}>
              <InputNumber min={0} step={0.1} placeholder="例如 10.0" />
            </Form.Item>
            <Form.Item
              label="固液比文本"
              name={['conditions', 'solid_liquid_ratio', 'ratio_text']}
              tooltip="输入固体质量和液体质量后会自动计算为 g:g"
            >
              <Input
                readOnly
                placeholder="输入质量后自动计算，例如 1:10 g:g"
              />
            </Form.Item>
          </Space>

          <Divider orientation="left">
            {isLiquidFormed ? '浸出效率测量（长表模式，液体形成时必填）' : '浸出效率测量（长表模式，可选）'}
          </Divider>

          {!isLiquidFormed && (
            <Alert
              type="info"
              showIcon
              message="液体未形成时，浸出效率测量可不填写"
              description="如果 DES 液体未形成，您可以不添加测量记录；如需记录，请不要填写浸出效率（留空或填写 0），并在观察/备注中说明。"
              style={{ marginBottom: 16 }}
            />
          )}
          <Form.List name="measurements">
            {(fields, { add, remove }) => (
              <>
                {fields.map((field) => (
                  <Card key={field.key} type="inner" style={{ marginBottom: 12 }}>
                    <Space align="baseline" wrap>
                      <Form.Item
                        {...field}
                        label="目标物质"
                        name={[field.name, 'target_material']}
                        rules={[{ required: true, message: '请填写目标物质' }]}
                      >
                        <Input placeholder="例如 cellulose" style={{ width: 160 }} />
                      </Form.Item>
                      <Form.Item
                        {...field}
                        label="时间 (h)"
                        name={[field.name, 'time_h']}
                        rules={[{ required: true, message: '请填写时间' }]}
                      >
                        <InputNumber min={0} step={0.5} style={{ width: 120 }} />
                      </Form.Item>
                      <Form.Item
                        {...field}
                        label="浸出效率"
                        name={[field.name, 'leaching_efficiency']}
                        rules={[{ type: 'number', min: 0, message: '必须为非负数' }]}
                      >
                        <InputNumber
                          min={0}
                          max={isLiquidFormed ? undefined : 0}
                          step={0.1}
                          style={{ width: 140 }}
                          placeholder={isLiquidFormed ? '至少一条需填写' : '留空或 0'}
                        />
                      </Form.Item>
                      <Form.Item
                        {...field}
                        label="单位"
                        name={[field.name, 'unit']}
                      >
                        <Input style={{ width: 90 }} placeholder="%" />
                      </Form.Item>
                      <Form.Item
                        {...field}
                        label="观察/备注"
                        name={[field.name, 'observation']}
                      >
                        <Input placeholder="可选" style={{ width: 220 }} />
                      </Form.Item>
                      <Button danger type="link" onClick={() => remove(field.name)}>
                        删除
                      </Button>
                    </Space>
                  </Card>
                ))}
                <Button
                  type="dashed"
                  onClick={() => {
                    const existing: any[] = form.getFieldValue('measurements') || [];
                    const last = existing[existing.length - 1];
                    const timeSeq = [1, 3, 6, 12, 24];
                    const nextTime =
                      last?.time_h !== undefined && last?.time_h !== null
                        ? (timeSeq.includes(last.time_h) && timeSeq[timeSeq.indexOf(last.time_h) + 1]) || last.time_h + 1
                        : 1;
                    add({
                      target_material: last?.target_material || detail.task?.target_material,
                      time_h: nextTime || 1,
                      unit: last?.unit || '%',
                    });
                  }}
                  block
                >
                  添加测量
                </Button>
              </>
            )}
          </Form.List>

          <Divider orientation="left">其他性质（可选）</Divider>

          <Form.Item
            label="其他观察到的性质"
            name="properties_text"
            help="每行一个属性，格式: 属性名=值"
          >
            <TextArea
              rows={4}
              placeholder="例如:
viscosity=low
color=transparent
stability=good"
              onChange={(e) => {
                const text = e.target.value;
                if (!text) {
                  form.setFieldValue('properties', undefined);
                  return;
                }

                const properties: Record<string, string> = {};
                const lines = text.split('\n');
                for (const line of lines) {
                  const [key, value] = line.split('=').map((s) => s.trim());
                  if (key && value) {
                    properties[key] = value;
                  }
                }
                form.setFieldValue('properties', properties);
              }}
            />
          </Form.Item>

          <Form.Item name="properties" hidden>
            <Input />
          </Form.Item>

          <Form.Item label="备注" name="notes">
            <TextArea
              rows={3}
              placeholder="请记录实验过程中的任何额外观察、问题或建议"
              showCount
              maxLength={500}
            />
          </Form.Item>

          <Form.Item>
            <Space>
              <Button
                type="primary"
                htmlType="submit"
                icon={<SendOutlined />}
                loading={submitting}
                size="large"
              >
                {detail.status === 'COMPLETED' ? '更新反馈' : '提交反馈'}
              </Button>
              <Button
                onClick={() => navigate(`/recommendations/${id}`)}
                size="large"
              >
                取消
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
}

export default FeedbackPage;
