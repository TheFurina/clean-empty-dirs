#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
清理空文件夹脚本

用法:
    # 清理指定目录
    python clean_empty_dirs.py -p "D:\\Downloads"
    python clean_empty_dirs.py --path "/home/user/data"

    # 清理本机所有磁盘/根目录 (Windows 扫描所有固定盘, 其他系统扫描 /)
    python clean_empty_dirs.py --all

    # 预览 (只显示将删除的空文件夹, 不实际删除)
    python clean_empty_dirs.py -p "D:\\Downloads" --dry-run

    # 跳过确认提示直接执行
    python clean_empty_dirs.py -p "D:\\Downloads" --yes

    # 高级过滤: 排除名称、最小空置天数、跳过隐藏目录、最大深度、记录日志
    python clean_empty_dirs.py -p "D:\\Downloads" \
        --exclude "tmp*" --exclude "__pycache__" \
        --min-age-days 7 --skip-hidden --max-depth 10 \
        --log clean.log

    # 静默模式 (适合计划任务/cron)
    python clean_empty_dirs.py -p "D:\\Downloads" --yes --quiet --log clean.log

    # 删除时记录被删空文件夹路径到文件, 以便事后恢复
    python clean_empty_dirs.py -p "D:\\Downloads" --yes --record deleted.txt

    # 从记录文件恢复空文件夹 (重新创建), 可按名称搜索过滤
    python clean_empty_dirs.py --recover deleted.txt --search "cache*"
    python clean_empty_dirs.py --recover deleted.txt --dry-run   # 先预览将恢复哪些
    python clean_empty_dirs.py --recover deleted.txt             # 恢复全部

    # 不传任何参数, 进入交互式询问模式
    python clean_empty_dirs.py
"""

import argparse
import fnmatch
import os
import sys
import time


# --all 模式排除的系统/关键目录 (路径片段匹配, 大小写不敏感)
SYSTEM_EXCLUDE_PARTS = {
    # Windows
    "c:\\windows", "c:\\program files", "c:\\program files (x86)",
    "c:\\programdata", "c:\\$recycle.bin", "c:\\system volume information",
    "c:\\recovery", "c:\\windowsapps",
    # Linux / macOS
    "/proc", "/sys", "/dev", "/run", "/snap", "/boot", "/var/lib/docker",
    "/var/lib/containerd",
}

# --all 模式排除的目录名 (任一层级出现即跳过该子树)
SYSTEM_EXCLUDE_NAMES = {
    ".git", ".svn", "node_modules", "__pycache__", ".cache",
    "$recycle.bin", "system volume information", "$winreagent",
}

# ANSI 颜色码
_COLOR = {
    "red":    "\033[31m",
    "green":  "\033[32m",
    "yellow": "\033[33m",
    "cyan":   "\033[36m",
    "gray":   "\033[90m",
    "bold":   "\033[1m",
    "reset":  "\033[0m",
}
# 是否启用彩色输出 (运行时按 tty / --no-color 决定)
USE_COLOR = False


def color(name: str) -> str:
    return _COLOR[name] if USE_COLOR else ""


def enable_windows_vt():
    """在 Windows 终端启用 ANSI 颜色支持"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        ENABLE_VT = 0x0004
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), ENABLE_VT)
    except Exception:
        pass


def is_hidden(path: str) -> bool:
    """判断目录是否为隐藏 (Windows 属性 / 类 Unix 点开头)"""
    name = os.path.basename(path)
    if name.startswith("."):
        return True
    if sys.platform == "win32":
        try:
            import ctypes
            attrs = ctypes.windll.kernel32.GetFileAttributesW(path)
            if attrs != 0xFFFFFFFF and (attrs & 0x2):  # FILE_ATTRIBUTE_HIDDEN
                return True
        except Exception:
            pass
    return False


def is_excluded(path: str) -> bool:
    """判断路径是否在系统排除列表中"""
    norm = os.path.normpath(path).lower()
    for part in SYSTEM_EXCLUDE_PARTS:
        if norm == part.lower() or norm.startswith(part.lower() + os.sep):
            return True
    for seg in norm.split(os.sep):
        if seg in SYSTEM_EXCLUDE_NAMES:
            return True
    return False


def matches_patterns(name: str, patterns) -> bool:
    """检查目录名是否匹配任一通配符模式 (大小写不敏感)"""
    lower = name.lower()
    for p in patterns:
        if fnmatch.fnmatch(lower, p.lower()):
            return True
    return False


def list_all_roots():
    """返回需要扫描的所有根路径"""
    if sys.platform == "win32":
        roots = []
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(260)
            length = ctypes.windll.kernel32.GetLogicalDriveStringsW(len(buf), buf)
            drives = buf.value[:length].split("\x00")
            for d in drives:
                if not d:
                    continue
                if ctypes.windll.kernel32.GetDriveTypeW(d) == 3:  # DRIVE_FIXED
                    roots.append(d.rstrip("\\"))
        except Exception as e:
            print(f"[WARN] 枚举磁盘失败, 回退到当前盘符: {e}")
            roots = [os.path.splitdrive(os.getcwd())[0] + os.sep]
        return roots
    else:
        return ["/"]


class Stats:
    """单次清理的统计信息"""
    def __init__(self):
        self.scanned = 0
        self.deleted = 0
        self.failed = 0
        self.denied = 0
        self.skipped = 0
        self.start = time.time()

    @property
    def elapsed(self):
        return time.time() - self.start


def clean_one_root(root, dry_run, stats, *,
                   skip_hidden=False, exclude_patterns=None,
                   include_patterns=None, min_age_days=0, max_depth=0,
                   logger=None, quiet=False, record_file=None):
    """清理单个根路径下的空文件夹.

    两遍策略: 先 topdown 裁剪子树, 再自底向上级联删除.
    """
    def log(msg):
        if not quiet:
            print(msg)
        if logger:
            logger.write(msg + "\n")
            logger.flush()

    if not os.path.isdir(root):
        log(f"{color('yellow')}[SKIP]{color('reset')} 路径不存在或不是目录: {root}")
        return

    log(f"\n{color('cyan')}=== 扫描: {root} ==={color('reset')}")

    root_norm = os.path.normpath(root).lower()

    def depth_of(p):
        rel = os.path.relpath(p, root)
        return 0 if rel == "." else rel.count(os.sep) + 1

    # 第 1 遍: 自顶向下, 裁剪被排除/隐藏/超深的子树
    candidates = []
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        stats.scanned += 1
        cur_depth = depth_of(dirpath)

        kept = []
        for d in dirnames:
            child = os.path.join(dirpath, d)
            if is_excluded(child):
                continue
            if skip_hidden and is_hidden(child):
                continue
            if exclude_patterns and matches_patterns(d, exclude_patterns):
                continue
            if max_depth > 0 and cur_depth + 1 > max_depth:
                continue
            kept.append(d)
        dirnames[:] = kept

        if os.path.normpath(dirpath).lower() != root_norm:
            candidates.append(dirpath)

    # 第 2 遍: 自底向上, 实现级联删除
    candidates.sort(key=lambda p: (p.count(os.sep), len(p)), reverse=True)

    for full in candidates:
        name = os.path.basename(full)

        if include_patterns and not matches_patterns(name, include_patterns):
            stats.skipped += 1
            continue

        try:
            entries = os.listdir(full)
        except (PermissionError, OSError) as e:
            log(f"{color('red')}[DENIED]{color('reset')} {full} ({e})")
            stats.denied += 1
            continue

        if not entries:
            if min_age_days > 0:
                try:
                    mtime = os.path.getmtime(full)
                except OSError:
                    stats.failed += 1
                    continue
                age_days = (time.time() - mtime) / 86400
                if age_days < min_age_days:
                    stats.skipped += 1
                    continue

            action = "DRY-RUN" if dry_run else "DELETE"
            tag = color("green") if dry_run else color("yellow")
            log(f"{tag}[{action}]{color('reset')} {full}")
            if not dry_run:
                try:
                    os.rmdir(full)
                    stats.deleted += 1
                    # 记录被删路径 (自底向上, 记录顺序即恢复顺序)
                    if record_file is not None:
                        try:
                            record_file.write(full + "\n")
                            record_file.flush()
                        except OSError as e:
                            log(f"  {color('red')}-> 记录失败: {e}{color('reset')}")
                except OSError as e:
                    log(f"  {color('red')}-> 失败: {e}{color('reset')}")
                    stats.failed += 1
            else:
                stats.deleted += 1


def recover_folders(record_path, search_pattern=None, dry_run=False,
                    yes=False, logger=None, quiet=False):
    """从记录文件读取被删空文件夹路径并重新创建.

    记录顺序为自底向上, 故按文件顺序 makedirs 即可级联创建.
    search_pattern 匹配 basename 或完整路径其一即恢复.
    """
    def out(msg):
        if not quiet:
            print(msg)
        if logger:
            logger.write(msg + "\n")
            logger.flush()

    if not os.path.isfile(record_path):
        out(f"{color('yellow')}[SKIP]{color('reset')} 记录文件不存在: {record_path}")
        return 1

    # 读取待恢复路径
    targets = []
    try:
        with open(record_path, "r", encoding="utf-8") as f:
            for line in f:
                p = line.rstrip("\n").rstrip("\r")
                if not p:
                    continue
                targets.append(p)
    except OSError as e:
        out(f"{color('red')}[ERROR]{color('reset')} 读取记录文件失败: {e}")
        return 1

    if not targets:
        out("记录文件为空, 无可恢复项.")
        return 0

    out("=" * 60)
    out(f"恢复模式: {'预览(DRY-RUN)' if dry_run else '实际恢复'}")
    out(f"记录文件: {record_path}")
    out(f"待恢复总数: {len(targets)}")
    if search_pattern:
        out(f"搜索过滤: {search_pattern}")
    out("=" * 60)

    # 名称过滤: basename 或完整路径匹配其一
    matched = []
    skipped = 0
    for p in targets:
        base = os.path.basename(p)
        if search_pattern:
            hit = (fnmatch.fnmatch(base.lower(), search_pattern.lower()) or
                    fnmatch.fnmatch(p.lower(), search_pattern.lower()) or
                    search_pattern.lower() in p.lower())
            if not hit:
                skipped += 1
                continue
        matched.append(p)

    if search_pattern:
        out(f"匹配 {len(matched)} 项, 跳过 {skipped} 项")

    if not matched:
        out("无匹配项, 退出.")
        return 0

    # 删除前确认
    if not dry_run and not yes:
        out(f"\n警告: 即将重新创建 {len(matched)} 个空文件夹, 此操作不可逆!")
        if not confirm("确定继续? (y/N): "):
            print("已取消.")
            return 0

    created = 0
    existed = 0
    failed = 0
    for p in matched:
        if os.path.exists(p):
            existed += 1
            action = "EXISTS"
            tag = color("gray")
        else:
            action = "DRY-RUN" if dry_run else "RESTORE"
            tag = color("green") if dry_run else color("yellow")
        out(f"{tag}[{action}]{color('reset')} {p}")

        if not dry_run and action != "EXISTS":
            try:
                os.makedirs(p, exist_ok=True)
                created += 1
            except OSError as e:
                out(f"  {color('red')}-> 失败: {e}{color('reset')}")
                failed += 1
        elif dry_run and action != "EXISTS":
            created += 1

    out("\n" + "=" * 60)
    verb = "将恢复" if dry_run else "已恢复"
    out(f"完成. {verb}空文件夹: {color('green')}{created}{color('reset')}")
    if existed:
        out(f"已存在跳过: {existed}")
    if failed:
        out(f"失败: {color('red')}{failed}{color('reset')}")
    out("=" * 60)
    return 0


def confirm(prompt: str) -> bool:
    """交互式确认 (非交互环境直接返回 False)"""
    try:
        ans = input(prompt).strip().lower()
    except EOFError:
        return False
    return ans in ("y", "yes", "是", "ok")


def ask_int(prompt, default=0):
    """交互式输入整数, 留空用默认值"""
    try:
        s = input(prompt).strip()
    except EOFError:
        return default
    if not s:
        return default
    try:
        return int(s)
    except ValueError:
        print(f"无效数字, 使用默认值 {default}")
        return default


def main():
    global USE_COLOR

    parser = argparse.ArgumentParser(
        description="清理空文件夹 (支持指定目录或全部磁盘, 含多种过滤选项)"
    )
    parser.add_argument("-p", "--path", help="要清理的指定目录路径")
    parser.add_argument("--all", action="store_true",
                        help="清理本机所有位置 (Windows=所有固定盘, 其他=/根目录)")
    parser.add_argument("--dry-run", action="store_true", help="只预览, 不实际删除")
    parser.add_argument("-y", "--yes", action="store_true", help="跳过确认提示, 直接执行删除")
    parser.add_argument("--exclude", action="append", default=[],
                        metavar="PATTERN", help="排除匹配的目录名 (通配符, 可多次)")
    parser.add_argument("--include", action="append", default=[],
                        metavar="PATTERN", help="仅清理匹配的目录名 (通配符, 可多次)")
    parser.add_argument("--min-age-days", type=int, default=0,
                        metavar="N", help="仅清理已空置 N 天以上的目录 (按 mtime)")
    parser.add_argument("--max-depth", type=int, default=0,
                        metavar="N", help="最大扫描深度 (0=不限)")
    parser.add_argument("--skip-hidden", action="store_true", help="跳过隐藏目录")
    parser.add_argument("--log", metavar="FILE", help="将操作日志写入文件")
    parser.add_argument("--record", metavar="FILE",
                        help="删除时将被删空文件夹路径记录到此文件, 以便事后恢复")
    parser.add_argument("--recover", metavar="FILE",
                        help="恢复模式: 从记录文件重新创建空文件夹 (与 --path/--all 互斥)")
    parser.add_argument("--search", metavar="PATTERN",
                        help="恢复模式下按名称搜索过滤要恢复的文件夹 (通配符/子串, 大小写不敏感)")
    parser.add_argument("-q", "--quiet", action="store_true", help="静默模式 (不输出到控制台)")
    parser.add_argument("--no-color", action="store_true", help="禁用彩色输出")
    args = parser.parse_args()

    # 恢复模式与清理模式互斥
    if args.recover:
        if args.path or args.all:
            parser.error("--recover 与 --path/--all 互斥")
        if args.record:
            parser.error("--recover 与 --record 互斥")
        if args.exclude or args.include or args.min_age_days or args.max_depth or args.skip_hidden:
            parser.error("--recover 模式下不支持清理过滤参数 (--exclude/--include/--min-age-days/--max-depth/--skip-hidden)")

    if args.path and args.all:
        parser.error("--path 与 --all 不能同时使用")

    # 彩色输出: tty 自动开启, --no-color / --quiet 关闭
    if not args.no_color and sys.stdout.isatty():
        USE_COLOR = True
        enable_windows_vt()
    if args.quiet:
        USE_COLOR = False

    # 日志文件
    logger = None
    if args.log:
        try:
            logger = open(args.log, "w", encoding="utf-8")
        except OSError as e:
            print(f"[WARN] 无法打开日志文件 {args.log}: {e}")

    def banner(msg):
        if not args.quiet:
            print(msg)
        if logger:
            logger.write(msg + "\n")

    # 恢复模式: 直接调用后退出
    if args.recover:
        return recover_folders(
            args.recover,
            search_pattern=args.search,
            dry_run=args.dry_run,
            yes=args.yes,
            logger=logger,
            quiet=args.quiet,
        )

    # 无参数时进入交互模式
    if not args.path and not args.all and not args.recover:
        banner("=" * 60)
        banner("  空文件夹清理工具 (交互模式)")
        banner("=" * 60)
        banner("请选择操作:")
        banner("  1) 清理 - 指定目录")
        banner("  2) 清理 - 本机所有位置 (排除系统目录)")
        banner("  3) 恢复 - 从记录文件重建被删空文件夹")
        banner("  0) 退出")
        try:
            choice = input("请输入选项 [1/2/3/0]: ").strip()
        except EOFError:
            print("\n非交互环境无法使用交互模式, 请通过命令行参数指定.")
            return 1

        if choice in ("0", "q", "quit", "exit"):
            print("已退出.")
            return 0
        elif choice == "1":
            try:
                path = input("请输入要清理的目录路径: ").strip()
            except EOFError:
                print("\n输入被中断, 已取消.")
                return 1
            if not path:
                print("路径为空, 已取消.")
                return 1
            if not os.path.isdir(path):
                print(f"目录不存在或不是有效目录: {path}")
                return 1
            args.path = path
        elif choice == "2":
            args.all = True
        elif choice == "3":
            # 恢复模式
            try:
                rec_file = input("请输入记录文件路径: ").strip()
            except EOFError:
                print("\n输入被中断, 已取消.")
                return 1
            if not rec_file:
                print("记录文件路径为空, 已取消.")
                return 1
            if not os.path.isfile(rec_file):
                print(f"记录文件不存在: {rec_file}")
                return 1
            try:
                dry = input("是否先预览(只显示不恢复)? (Y/n): ").strip().lower()
            except EOFError:
                dry = ""
            dry_run = dry not in ("n", "no", "否")
            yes = False
            if not dry_run:
                try:
                    skip = input("跳过恢复前二次确认? (y/N): ").strip().lower()
                except EOFError:
                    skip = ""
                yes = skip in ("y", "yes", "是")
            try:
                kw = input("按名称搜索过滤 (通配符/子串, 留空=全部恢复): ").strip()
            except EOFError:
                kw = ""
            search = kw if kw else None
            if logger:
                logger.close()
            return recover_folders(
                rec_file,
                search_pattern=search,
                dry_run=dry_run,
                yes=yes,
                logger=open(args.log, "w", encoding="utf-8") if args.log else None,
                quiet=args.quiet,
            )
        else:
            print(f"无效选项: {choice}")
            return 1

        # 预览/确认
        try:
            dry = input("是否先预览(只显示不删除)? (Y/n): ").strip().lower()
        except EOFError:
            dry = ""
        args.dry_run = dry not in ("n", "no", "否")

        if not args.dry_run:
            try:
                skip = input("跳过删除前二次确认? (y/N): ").strip().lower()
            except EOFError:
                skip = ""
            args.yes = skip in ("y", "yes", "是")

        # 高级过滤
        try:
            adv = input("配置高级过滤? (y/N): ").strip().lower()
        except EOFError:
            adv = ""
        if adv in ("y", "yes", "是"):
            try:
                ex = input("排除的目录名模式 (通配符, 逗号分隔, 留空跳过): ").strip()
            except EOFError:
                ex = ""
            if ex:
                args.exclude = [s.strip() for s in ex.split(",") if s.strip()]
            try:
                inc = input("仅清理的目录名模式 (逗号分隔, 留空跳过): ").strip()
            except EOFError:
                inc = ""
            if inc:
                args.include = [s.strip() for s in inc.split(",") if s.strip()]
            args.min_age_days = ask_int("最小空置天数 (留空=0): ", 0)
            args.max_depth = ask_int("最大扫描深度 (留空=0=不限): ", 0)
            try:
                hid = input("跳过隐藏目录? (y/N): ").strip().lower()
            except EOFError:
                hid = ""
            args.skip_hidden = hid in ("y", "yes", "是")

        # 记录被删路径以便恢复
        if not args.dry_run:
            try:
                rec = input("将被删文件夹路径记录到文件以便恢复? (y/N): ").strip().lower()
            except EOFError:
                rec = ""
            if rec in ("y", "yes", "是"):
                # 默认: 脚本同级目录
                script_dir = os.path.dirname(os.path.abspath(__file__))
                default_rec = os.path.join(script_dir, "deleted_dirs.txt")
                try:
                    rec_file = input(f"记录文件路径 [默认 {default_rec}]: ").strip()
                except EOFError:
                    rec_file = ""
                args.record = rec_file if rec_file else default_rec

    # 扫描目标
    if args.all:
        roots = list_all_roots()
        scope = "所有位置 (排除系统目录)"
    else:
        roots = [args.path]
        scope = args.path

    mode = "预览(DRY-RUN)" if args.dry_run else "实际删除"
    banner("=" * 60)
    banner(f"扫描范围: {scope}")
    banner(f"执行模式: {mode}")
    banner(f"扫描根数: {len(roots)}")
    if args.exclude:
        banner(f"排除模式: {args.exclude}")
    if args.include:
        banner(f"包含模式: {args.include}")
    if args.min_age_days > 0:
        banner(f"最小空置: {args.min_age_days} 天")
    if args.max_depth > 0:
        banner(f"最大深度: {args.max_depth}")
    if args.skip_hidden:
        banner("跳过隐藏目录: 是")
    if args.log:
        banner(f"日志文件: {args.log}")
    if args.record:
        banner(f"记录文件: {args.record}")
    banner("=" * 60)

    # 删除前确认
    if not args.dry_run and not args.yes:
        banner("\n警告: 即将实际删除空文件夹, 此操作不可逆!")
        if not confirm("确定继续? (y/N): "):
            print("已取消.")
            return 0

    # 记录文件句柄 (仅实际删除时打开)
    record_file = None
    if args.record and not args.dry_run:
        try:
            record_file = open(args.record, "w", encoding="utf-8")
        except OSError as e:
            print(f"[WARN] 无法打开记录文件 {args.record}: {e}")

    stats = Stats()
    for root in roots:
        clean_one_root(
            root, args.dry_run, stats,
            skip_hidden=args.skip_hidden,
            exclude_patterns=args.exclude,
            include_patterns=args.include,
            min_age_days=args.min_age_days,
            max_depth=args.max_depth,
            logger=logger, quiet=args.quiet,
            record_file=record_file,
        )

    if record_file:
        record_file.close()
        if not args.quiet:
            print(f"删除记录已保存: {args.record} (可用 --recover {args.record} 恢复)")

    banner("\n" + "=" * 60)
    verb = "将删除" if args.dry_run else "已删除"
    banner(f"完成. {verb}空文件夹: {color('green')}{stats.deleted}{color('reset')}")
    banner(f"扫描目录数: {stats.scanned}")
    if stats.skipped:
        banner(f"跳过(过滤): {stats.skipped}")
    if stats.denied:
        banner(f"权限拒绝: {color('red')}{stats.denied}{color('reset')}")
    if stats.failed:
        banner(f"其他失败: {color('red')}{stats.failed}{color('reset')}")
    banner(f"耗时: {stats.elapsed:.2f} 秒")
    banner("=" * 60)

    if logger:
        logger.close()
        if not args.quiet:
            print(f"日志已保存: {args.log}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
