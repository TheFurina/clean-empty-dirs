# clean-empty-dirs

一个跨平台的空文件夹清理工具,支持指定目录或全盘扫描、丰富的过滤选项、删除记录与按名称搜索恢复,以及交互式操作。纯 Python 标准库实现,无需安装第三方依赖。

## 功能特性

- **双模式清理**:指定目录(`-p`)或本机所有固定磁盘(`--all`)
- **级联删除**:自底向上遍历,子空目录删除后父目录若变空也会一并清理
- **子树裁剪**:被排除/隐藏/超深的目录整棵子树都不会被扫描(性能更优)
- **安全防护**:
  - `--dry-run` 预览模式,只显示不删除
  - 删除前交互确认(可用 `-y` 跳过)
  - `--all` 模式默认排除系统目录(`C:\Windows`、`/proc`、`/sys` 等)和版本控制目录(`.git`、`node_modules`)
- **高级过滤**:
  - `--exclude` 排除匹配名称的目录(通配符)
  - `--include` 仅清理匹配名称的目录
  - `--min-age-days` 仅清理已空置 N 天以上的目录
  - `--max-depth` 限制扫描深度
  - `--skip-hidden` 跳过隐藏目录
- **删除记录与恢复**:
  - `--record` 删除时记录被删路径到文件,以便事后恢复
  - `--recover` 从记录文件重建被删空文件夹
  - `--search` 恢复时按名称搜索过滤(通配符/子串)
- **运维友好**:
  - `--log` 写入操作日志
  - `-q/--quiet` 静默模式,适合 cron/计划任务
  - 完整统计(扫描数/删除/跳过/权限拒绝/失败/耗时)
- **跨平台**:Windows / Linux / macOS 通用,自动处理权限错误
- **零依赖**:仅使用 Python 标准库

## 快速开始

### 环境要求

- Python 3.6+

### 安装

无需安装,直接运行脚本即可:

```bash
git clone https://github.com/TheFurina/clean-empty-dirs.git
cd clean-empty-dirs
```

### 基本用法

```bash
# 清理指定目录(预览)
python clean_empty_dirs.py -p "D:\Downloads" --dry-run

# 清理指定目录(实际执行)
python clean_empty_dirs.py -p "D:\Downloads" --yes

# 清理本机所有磁盘
python clean_empty_dirs.py --all --dry-run
python clean_empty_dirs.py --all --yes

# 交互模式(无参数时进入)
python clean_empty_dirs.py
```

## 使用示例

### 1. 高级过滤组合

```bash
python clean_empty_dirs.py -p "D:\Downloads" \
    --exclude "tmp*" --exclude "__pycache__" \
    --min-age-days 7 --skip-hidden --max-depth 10 \
    --log clean.log
```

### 2. 删除记录与恢复(误删可回滚)

```bash
# 步骤 1:清理并记录被删路径
python clean_empty_dirs.py -p "D:\Downloads" --yes --record deleted.txt

# 步骤 2:发现误删,先预览将恢复哪些
python clean_empty_dirs.py --recover deleted.txt --search "cache*" --dry-run

# 步骤 3:确认后按名称恢复(仅恢复匹配项)
python clean_empty_dirs.py --recover deleted.txt --search "cache*" --yes

# 或全部恢复
python clean_empty_dirs.py --recover deleted.txt --yes
```

### 3. 静默模式(适合 cron / 计划任务)

```bash
# Linux cron 示例:每天凌晨 3 点清理临时目录并记录日志
0 3 * * * /usr/bin/python3 /path/to/clean_empty_dirs.py \
    -p /tmp --yes --quiet --log /var/log/clean.log

# Windows 计划任务:同理使用 --quiet --log
```

### 4. 交互模式

```text
$ python clean_empty_dirs.py
============================================================
  空文件夹清理工具 (交互模式)
============================================================
请选择操作:
  1) 清理 - 指定目录
  2) 清理 - 本机所有位置 (排除系统目录)
  3) 恢复 - 从记录文件重建被删空文件夹
  0) 退出
请输入选项 [1/2/3/0]:
```

交互模式逐步询问:清理范围 → 是否预览 → 高级过滤 → 是否记录被删路径(默认存到脚本同级目录 `deleted_dirs.txt`)。恢复分支(选项 3)询问:记录文件路径 → 是否预览 → 名称搜索。

## 命令行参数

| 参数 | 说明 |
|------|------|
| `-p, --path PATH` | 要清理的指定目录路径 |
| `--all` | 清理本机所有位置(Windows=所有固定盘,其他=/根目录) |
| `--dry-run` | 只预览,不实际删除 |
| `-y, --yes` | 跳过确认提示,直接执行 |
| `--exclude PATTERN` | 排除匹配的目录名(通配符,可多次) |
| `--include PATTERN` | 仅清理匹配的目录名(通配符,可多次) |
| `--min-age-days N` | 仅清理已空置 N 天以上的目录(按 mtime) |
| `--max-depth N` | 最大扫描深度(0=不限) |
| `--skip-hidden` | 跳过隐藏目录 |
| `--log FILE` | 将操作日志写入文件 |
| `--record FILE` | 删除时记录被删路径到此文件以便恢复 |
| `--recover FILE` | 恢复模式:从记录文件重建空文件夹 |
| `--search PATTERN` | 恢复时按名称搜索过滤(通配符/子串) |
| `-q, --quiet` | 静默模式(不输出到控制台) |
| `--no-color` | 禁用彩色输出 |

## 工作机制

### 级联清理(两遍算法)

1. **第 1 遍**:自顶向下遍历,通过修改 `dirnames` 裁剪被排除/隐藏/超深的整棵子树,收集候选目录
2. **第 2 遍**:按路径深度从深到浅排序处理,删除前重新 `listdir` 检查空状态,实现级联删除

这样既正确跳过整棵子树(连后代都不扫描),又保证级联清理(子空目录删后父目录变空也会被删)。

### 记录与恢复

- **记录文件格式**:每行一个绝对路径,按自底向上(深→浅)顺序写入
- **恢复机制**:直接按文件顺序 `os.makedirs(exist_ok=True)`,父目录自动创建
- **搜索匹配**:basename 或完整路径 `fnmatch` 匹配其一,或子串包含

### 输出状态标签

| 标签 | 含义 |
|------|------|
| `[DRY-RUN]` | 预览模式,不实际操作 |
| `[DELETE]` | 实际删除空目录 |
| `[RESTORE]` | 实际创建空目录 |
| `[EXISTS]` | 已存在跳过(避免重复) |
| `[DENIED]` | 权限拒绝 |
| `[SKIP]` | 路径无效或记录文件不存在 |
| `[ERROR]` | 读取记录文件失败 |

## 安全说明

- **默认安全**:不预览直接删除风险高,故交互模式预览默认为 `Y`
- **系统目录保护**:`--all` 模式默认排除 `C:\Windows`、`C:\Program Files`、`/proc`、`/sys` 等关键目录
- **文件保护**:从不删除带文件的目录,只清理真正的空目录
- **不可逆提示**:实际删除前会明确警告"此操作不可逆"
- **可恢复**:`--record` 记录被删路径,`--recover` 可按需恢复
- **互斥保护**:`--recover` 与 `--path/--all/--record` 冲突时立即报错

## 跨平台支持

| 平台 | 全盘扫描 | 隐藏目录检测 |
|------|----------|--------------|
| Windows | 枚举所有固定磁盘(`GetLogicalDriveStringsW`) | `FILE_ATTRIBUTE_HIDDEN` 属性 |
| Linux | 扫描 `/` 根目录 | 点开头的目录名 |
| macOS | 扫描 `/` 根目录 | 点开头的目录名 |

## 许可证

MIT License
