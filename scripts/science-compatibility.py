#!/usr/bin/env python3
"""Read-only local compatibility checks. Never start a daemon or read credentials."""
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
from datetime import datetime, timezone

sys.dont_write_bytecode = True
FLAGS = ('--port', '--no-browser', '--detached', '--sandbox-port')


def output(command):
    # Discard arbitrary stderr; return only parsed versions/capabilities to the UI.
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=5, check=False,
                                creationflags=0x08000000 if os.name == "nt" else 0)
        return result.stdout[:65536] if result.returncode == 0 else ''
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return ''


def version(text):
    match = re.search(r'(?<![\w.])(\d+\.\d+\.\d+)(?![\w.])', text)
    return match.group(1) if match else None


def inspect(home, binary, node=None, bundle=None, runner=output):
    home, binary = Path(home), Path(binary)
    checks = []

    def add(key, label, status, detail, action=''):
        checks.append(dict(id=key, label=label, status=status, detail=detail, action=action))

    executable = binary.is_file() and os.access(binary, os.X_OK)
    actual = version(runner([str(binary), '--version'])) if executable else None
    bundled = None
    try:
        if os.name == 'nt' and bundle is None:
            installed = Path(os.environ.get('LOCALAPPDATA', home / 'AppData/Local')) / 'Programs/ClaudeScience/claude-science.exe'
            bundled = version(runner([str(installed), '--version'])) if installed.is_file() else None
        with Path(bundle or '/Applications/Claude Science.app/Contents/Info.plist').open('rb') as f:
            bundled = version(str(plistlib.load(f).get('CFBundleShortVersionString', '')))
    except (OSError, ValueError, plistlib.InvalidFileException):
        pass
    add('binary', '实际启动版本', 'pass' if actual else 'error',
        f'Claude Science {actual}' if actual else '无法读取 Science 可执行文件的版本。',
        '' if actual else '请先安装或修复 Claude Science，再重新检查。')
    if actual:
        help_text = runner([str(binary), 'serve', '--help'])
        missing = [flag for flag in FLAGS if not re.search(re.escape(flag) + r'(?=\s|$)', help_text)]
        add('launch', '启动接口', 'pass' if not missing else 'warning',
            '所需启动参数均可用。' if not missing else '无法确认部分启动参数：' + '、'.join(missing),
            '' if not missing else '请检查 Science 安装；若刚更新，请使用匹配版本的 CSSwitch。')
    if actual:
        add('bundle', '自动更新版本', 'info',
            f'当前启动 {actual}；应用包 {bundled}。Science 自动更新后两者可以不同。'
            if bundled and bundled != actual else
            f'当前启动与应用包均为 {actual}。' if bundled else
            f'当前启动 {actual}；未识别应用包版本。')


    spec = importlib.util.spec_from_file_location('compat_profiles', Path(__file__).with_name('science-connectors.py'))
    profiles = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(profiles)
    runtime_path, runtime_version = None, None
    try:
        runtime_path = profiles.runtime(home)
        runtime_version = version(runtime_path.name.removesuffix('-release'))
        matched = actual is not None and runtime_version == actual
        add('runtime', '本机运行资源', 'pass' if matched else 'warning',
            f'完整资源 {runtime_version}。' if matched else f'资源 {runtime_version} 与实际启动版本未确认一致。',
            '' if matched else '请等待 Science 更新完成，再重新检查；暂不要手动删除旧资源。')
    except (ValueError, OSError):
        add('runtime', '本机运行资源', 'error', '未找到完整的 Science 运行资源。',
            '打开 Claude Science 完成资源下载后重新检查。')
    available, missing_profiles = 0, []
    for key, name in profiles.NAMES.items():
        try:
            profiles.command(home, key, node)
            available += 1
        except (ValueError, OSError):
            missing_profiles.append(name)
    add('connectors', '连接器资源', 'pass' if not missing_profiles else 'warning',
        f'{available}/{len(profiles.NAMES)} 项资源可用。' + (' 缺少：' + '、'.join(missing_profiles) if missing_profiles else ''),
        '资源检查不代表已连接。启动后可在 MCP 管理中检查状态或修复接入。' if not missing_profiles else '先修复 Science 资源和依赖，再到 MCP 管理中修复接入。')
    node_version = version(runner([str(node), '--version']).lstrip('v')) if node else None
    node_ok = bool(node_version and int(node_version.split('.')[0]) >= 22)
    add('node', 'Ketcher 运行环境', 'pass' if node_ok else 'warning',
        f'Node.js {node_version}' if node_version else '未找到可用的 Node.js。',
        '' if node_ok else '安装 Node.js 22 或更新版本后重新检查；其他连接器使用 Science 自带 Python。')
    add('python', '管理组件运行环境', 'pass', 'Python ' + '.'.join(map(str, sys.version_info[:3])))
    return dict(checked_at=datetime.now(timezone.utc).isoformat(),
                science_version=actual, bundle_version=bundled, binary=str(binary),
                runtime_version=runtime_version, runtime_path=str(runtime_path) if runtime_path else None,
                available_profiles=available, total_profiles=len(profiles.NAMES), checks=checks,
                status='error' if any(c['status'] == 'error' for c in checks) else
                       'warning' if any(c['status'] == 'warning' for c in checks) else 'pass')


if __name__ == '__main__':
    params = json.load(sys.stdin)
    print(json.dumps(inspect(params['home'], params['binary'], params.get('node')), ensure_ascii=False))
