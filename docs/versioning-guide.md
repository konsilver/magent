# Jingxin-Agent 版本管理与发布指南

## 目录

1. [版本号规范](#版本号规范)
2. [版本发布流程](#版本发布流程)
3. [Git 分支策略](#git-分支策略)
4. [变更日志管理](#变更日志管理)
5. [发布检查清单](#发布检查清单)
6. [回滚策略](#回滚策略)

---

## 版本号规范

### Semantic Versioning (语义化版本)

本项目遵循 [Semantic Versioning 2.0.0](https://semver.org/) 规范：

```
MAJOR.MINOR.PATCH[-PRERELEASE][+BUILD]
```

**示例：** `1.2.3-beta.1+20240115`

### 版本号组成

1. **MAJOR (主版本号)**
   - 不兼容的 API 变更
   - 重大架构调整
   - 破坏性功能变更
   - 示例：`1.0.0` → `2.0.0`

2. **MINOR (次版本号)**
   - 向后兼容的新功能
   - 功能增强
   - 新增 API 端点
   - 示例：`1.0.0` → `1.1.0`

3. **PATCH (修订号)**
   - 向后兼容的 bug 修复
   - 性能优化
   - 文档更新
   - 示例：`1.0.0` → `1.0.1`

4. **PRERELEASE (预发布标识，可选)**
   - `alpha`: 内部测试版本
   - `beta`: 公开测试版本
   - `rc`: 候选发布版本
   - 示例：`1.0.0-alpha.1`, `1.0.0-beta.2`, `1.0.0-rc.1`

5. **BUILD (构建元数据，可选)**
   - 构建日期
   - 构建号
   - 示例：`1.0.0+20240115`, `1.0.0-beta.1+build.123`

### 版本号决策树

```
是否有破坏性 API 变更？
  ├─ 是 → 升级 MAJOR
  └─ 否 → 是否新增功能？
           ├─ 是 → 升级 MINOR
           └─ 否 → 仅修复 bug？
                    ├─ 是 → 升级 PATCH
                    └─ 否 → 无需发布新版本
```

---

## 版本发布流程

### P0 发布 (0.0.x - MVP)

**目标：** 核心聊天功能生产可用

```bash
# 1. 从 develop 创建发布分支
git checkout develop
git pull origin develop
git checkout -b release/v0.0.1

# 2. 更新版本号
# 编辑 version.py 或 package.json
echo "__version__ = '0.0.1'" > core/version.py

# 3. 更新 CHANGELOG
# 添加发布日期和版本号

# 4. 提交版本变更
git add .
git commit -m "chore: bump version to 0.0.1"

# 5. 合并到 main 并打标签
git checkout main
git merge release/v0.0.1
git tag -a v0.0.1 -m "Release v0.0.1 - P0 MVP"
git push origin main --tags

# 6. 回合到 develop
git checkout develop
git merge release/v0.0.1
git push origin develop

# 7. 删除发布分支
git branch -d release/v0.0.1
```

### P1 发布 (0.1.x - 历史管理)

**目标：** 聊天历史与会话后端化

```bash
# 遵循与 P0 相同的流程，版本号改为 0.1.0
git checkout -b release/v0.1.0
# ... (相同步骤)
git tag -a v0.1.0 -m "Release v0.1.0 - P1 Chat History"
```

### P2 发布 (0.2.x - 能力中心)

**目标：** 能力中心与知识库后端化

```bash
git checkout -b release/v0.2.0
# ... (相同步骤)
git tag -a v0.2.0 -m "Release v0.2.0 - P2 Capability Center"
```

### 正式版发布 (1.0.0+)

**目标：** 生产稳定，所有核心功能完整

```bash
git checkout -b release/v1.0.0
# ... (相同步骤)
git tag -a v1.0.0 -m "Release v1.0.0 - First Stable Release"
```

---

## Git 分支策略

### 分支模型

```
main (生产)
  ├─ develop (开发主分支)
  │   ├─ feature/user-auth (功能分支)
  │   ├─ feature/chat-history (功能分支)
  │   └─ bugfix/session-leak (修复分支)
  ├─ release/v0.1.0 (发布分支)
  └─ hotfix/critical-security (热修复分支)
```

### 分支说明

1. **main**
   - 生产环境代码
   - 只能通过 release 或 hotfix 分支合并
   - 每次合并必须打 tag
   - 受保护分支，需要 PR + 审核

2. **develop**
   - 开发主分支
   - 集成所有已完成的功能
   - CI 自动部署到 staging
   - 受保护分支

3. **feature/***
   - 功能开发分支
   - 从 develop 创建
   - 完成后合并回 develop
   - 命名：`feature/短描述`
   - 示例：`feature/user-preferences-api`

4. **bugfix/***
   - Bug 修复分支
   - 从 develop 创建
   - 完成后合并回 develop
   - 命名：`bugfix/短描述`
   - 示例：`bugfix/session-timeout`

5. **release/***
   - 发布准备分支
   - 从 develop 创建
   - 只做版本号更新、文档更新、小 bug 修复
   - 完成后合并到 main 和 develop
   - 命名：`release/v版本号`
   - 示例：`release/v1.0.0`

6. **hotfix/***
   - 紧急修复分支
   - 从 main 创建
   - 直接修复生产问题
   - 完成后合并到 main 和 develop
   - 命名：`hotfix/短描述`
   - 示例：`hotfix/critical-memory-leak`

### 分支操作示例

#### 创建功能分支

```bash
git checkout develop
git pull origin develop
git checkout -b feature/user-profile-api
# 开发...
git add .
git commit -m "feat: add user profile API endpoints"
git push origin feature/user-profile-api
# 创建 PR: feature/user-profile-api → develop
```

#### 创建 Hotfix

```bash
git checkout main
git pull origin main
git checkout -b hotfix/security-patch
# 修复...
git add .
git commit -m "fix: patch critical security vulnerability"
git push origin hotfix/security-patch
# 创建 PR: hotfix/security-patch → main
# 创建 PR: hotfix/security-patch → develop
```

---

## 变更日志管理

### CHANGELOG.md 格式

遵循 [Keep a Changelog](https://keepachangelog.com/) 规范：

```markdown
# Changelog

## [Unreleased]

### Added
- 新功能 A
- 新功能 B

### Changed
- 变更 C

### Fixed
- 修复 D

## [1.0.0] - 2024-01-15

### Added
- 初始发布
- 功能 X
- 功能 Y

### Security
- 安全修复 Z

## [0.2.0] - 2024-01-10
...
```

### 变更类型

- **Added**: 新增功能
- **Changed**: 功能变更
- **Deprecated**: 即将废弃的功能
- **Removed**: 已移除功能
- **Fixed**: Bug 修复
- **Security**: 安全相关变更

### 自动化更新

```bash
# 使用脚本自动生成 CHANGELOG 条目
python scripts/changelog_generator.py --version 1.0.0 --type minor
```

---

## 发布检查清单

### 发布前检查 (Pre-Release Checklist)

#### 代码质量

- [ ] 所有 CI 测试通过
- [ ] 代码覆盖率 >= 80%
- [ ] 无已知的 P0/P1 bug
- [ ] 安全扫描通过 (bandit, safety)
- [ ] 性能测试通过

#### 文档

- [ ] CHANGELOG.md 已更新
- [ ] API 文档已更新 (api-contract.yaml)
- [ ] README.md 版本号已更新
- [ ] 迁移指南已编写 (如有 breaking changes)

#### 数据库

- [ ] 数据库迁移脚本已测试
- [ ] 回滚脚本已准备
- [ ] Staging 环境迁移演练成功

#### 配置

- [ ] 环境变量清单已更新
- [ ] 生产配置已验证
- [ ] 秘钥轮换已完成 (如需要)

#### 监控

- [ ] 新增指标已接入 Prometheus
- [ ] 告警规则已配置
- [ ] Dashboard 已更新

#### 团队

- [ ] 发布计划已同步团队
- [ ] On-call 人员已确认
- [ ] 回滚预案已准备

### 发布后验证 (Post-Release Verification)

- [ ] Health check 通过 (`/health`, `/ready`)
- [ ] 核心 API 端点可访问
- [ ] 错误率 < 0.1%
- [ ] P95 延迟正常
- [ ] 数据库连接池健康
- [ ] 监控指标正常
- [ ] 用户反馈收集

---

## 回滚策略

### 何时回滚

1. **立即回滚场景**
   - 核心功能不可用
   - 错误率 > 5%
   - P99 延迟 > 10x 基线
   - 数据损坏或丢失
   - 严重安全漏洞

2. **计划回滚场景**
   - 用户体验显著下降
   - 性能持续劣化
   - 非核心功能异常

### 回滚流程

#### 自动回滚 (Blue-Green)

```bash
# Load Balancer 切回 Blue 环境
ssh production-server << 'EOF'
  sudo /opt/scripts/switch-to-blue.sh
  # 验证 Blue 健康
  curl -f http://localhost:8000/health
EOF
```

#### 手动回滚 (Rolling)

```bash
# 回滚到前一个版本
ssh production-server << 'EOF'
  cd /opt/jingxin-agent
  git checkout v1.0.0  # 前一个稳定版本
  docker-compose pull
  docker-compose up -d --force-recreate
  # 验证
  sleep 30
  curl -f http://localhost:8000/health
EOF
```

#### 数据库回滚

```bash
# 1. 备份当前状态
alembic current > current_version.txt

# 2. 回滚到指定版本
alembic downgrade <target_revision>

# 3. 验证
alembic current
```

### 回滚后行动

1. **事后分析 (Post-Mortem)**
   - 记录故障时间线
   - 分析根本原因
   - 制定预防措施

2. **通知**
   - 用户通知 (如需要)
   - 团队复盘会议
   - 文档更新

3. **修复与重新发布**
   - 修复问题
   - 补充测试用例
   - 重新走发布流程

---

## 附录

### 版本号管理工具

#### version.py

```python
# core/version.py
__version__ = "1.0.0"
__build__ = "20240115"

def get_version():
    return f"{__version__}+{__build__}"
```

#### 版本号 API

```python
# api/routes/version.py
from core.version import get_version

@router.get("/api/version")
def version():
    return {
        "version": get_version(),
        "environment": os.getenv("ENV", "unknown")
    }
```

### 自动化脚本

#### 版本升级脚本

```bash
#!/bin/bash
# scripts/bump_version.sh

TYPE=$1  # major, minor, patch
CURRENT=$(grep __version__ core/version.py | cut -d'"' -f2)

# 计算新版本号
NEW_VERSION=$(python scripts/semver.py bump $TYPE $CURRENT)

# 更新文件
sed -i "s/__version__ = \".*\"/__version__ = \"$NEW_VERSION\"/" core/version.py

echo "Version bumped: $CURRENT → $NEW_VERSION"
```

### 发布模板

#### PR 模板

```markdown
## Release v1.0.0

### Changes
- Feature A
- Feature B
- Bug fix C

### Migration Required
- [ ] Database migration
- [ ] Configuration updates

### Testing
- [ ] Unit tests passed
- [ ] Integration tests passed
- [ ] Manual testing completed

### Documentation
- [ ] CHANGELOG updated
- [ ] API docs updated
- [ ] Migration guide written

### Deployment Plan
- Deploy to staging: 2024-01-10
- Deploy to production: 2024-01-15
- Rollout strategy: Blue-Green

/cc @team-leads @ops-team
```

---

## 联系与支持

- 发布负责人：[Name]
- On-call 团队：[Slack Channel]
- 紧急联系：[Phone/Email]

**最后更新：** 2024-01-XX
