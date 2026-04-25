interface BrandLoaderProps {
  /** 视觉尺寸（px），默认 16 */
  size?: number;
  /** 可访问性文案，缺省按 done 状态自动选择 */
  label?: string;
  /** true = 定格的品牌 mark（灰）；false = 正在绘制的 GIF（蓝） */
  done?: boolean;
}

/**
 * 统一的"进行中 / 已完成"行内指示器。
 * - active：`/loader.gif` 品牌蓝动态绘制
 * - done：`/loader-done.png` slate 灰定格终态 + 短促淡入
 *
 * 用于 ThinkingInline、ToolProgressInline 等 inline summary 位置，
 * 取代原先的 breathingOrbs + pulseDot 两套零散视觉。
 */
export function BrandLoader({ size = 16, label, done = false }: BrandLoaderProps) {
  return (
    <span
      className={`jx-brandLoader${done ? ' jx-brandLoader--done' : ''}`}
      role="img"
      aria-label={label ?? (done ? '已完成' : '加载中')}
      style={{ width: size, height: size }}
    />
  );
}

export default BrandLoader;
