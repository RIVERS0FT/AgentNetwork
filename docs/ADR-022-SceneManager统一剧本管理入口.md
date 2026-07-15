# ADR-022：SceneManager 统一剧本管理入口

## 状态

已采纳并实现。

## 背景

`SceneStorage` 已经能够处理单个剧本的 ZIP 导入、目录持久化、读取、解析、归档、可见性和删除，但批量任务、占用检查、逐项错误隔离和结果汇总不属于存储组件职责。若 API 直接编排这些流程，会形成重复业务逻辑，并使剧本管理再次绕过统一文件管理边界。

## 决策

新增 `agent_network/scene_manager.py`，由 `SceneManager` 作为剧本业务管理的唯一门面。

职责固定如下：

- `SceneManager` 负责编排单项和批量业务操作；
- `SceneManager` 负责批量任务标识、逐项错误隔离、成功失败统计和结果汇总；
- `SceneManager` 在删除前调用仿真域提供的占用检查器；
- `SceneManager` 负责将多个剧本资源组合为一个批量下载归档；
- `SceneStorage` 只负责单个剧本资源的导入、读取、解析、归档、可见性和删除；
- `SceneStorage` 的所有物理文件操作继续委托给 `FileManager`；
- API 层不得直接调用 `Path`、`open`、`ZipFile`、`unlink` 或 `shutil` 操作剧本文件；
- API 层不得自行实现批量循环、占用保护或结果汇总。

调用关系固定为：

```text
Scene API / Simulation Setup
        ↓
SceneManager
        ↓
SceneStorage
        ↓
FileManager
        ↓
受管文件系统
```

## 批量结果合同

所有批量操作返回：

```text
operation
batch_id
total
succeeded
failed
items[]
archive_resource_id
archive_name
```

每个 `items[]` 至少包含：

```text
operation
scene_key
success
status
resource_id
error_code
error
```

单个剧本失败不得终止同批次其他剧本的处理。

## 当前接口

```text
POST /api/scenes/batch/upload
POST /api/scenes/batch/download
GET  /api/scenes/batch/download/{resource_id}
POST /api/scenes/batch/delete
POST /api/scenes/batch/parse
```

批量上传请求中的每个 ZIP 使用 Base64 传输；Base64 解码失败只影响对应项。批量下载先创建由 `FileManager` 管理的归档资源，再通过下载接口流式返回。批量删除逐项检查活动仿真占用。批量解析逐项返回结构化 `SceneDefinition`。

## 禁止回退规则

后续修改不得：

1. 将批量编排重新放回 `SceneStorage`；
2. 在 API 中新增独立的批量循环和错误汇总；
3. 绕过 `FileManager` 读写、压缩、解压或删除剧本文件；
4. 因一个剧本失败而回滚或中止无关剧本项；
5. 在没有占用检查的情况下物理删除剧本；
6. 为批量下载返回服务器物理路径。
