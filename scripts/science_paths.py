"""Read-only Science location discovery shared by Windows desktop bridges."""
import json
import os
from pathlib import Path
import sys
import tomllib
import re


def expand(value, home, env=None):
    env = os.environ if env is None else env
    value = re.sub(r'%([^%]+)%|\$\{([^}]+)\}|\$(\w+)',
                   lambda m: str(env.get(next(g for g in m.groups() if g), m.group())), str(value))
    if value == '~': path = home
    elif value.startswith(('~/', '~\\')): path = home / value[2:]
    else: path = Path(value)
    if not path.is_absolute(): raise ValueError('Science 目录必须是绝对路径。')
    # Normalize Windows extended prefixes and case using the filesystem.
    return path.resolve()


def has_history(root):
    return (root / 'operon-cli.db').is_file() or any(root.glob('orgs/*/operon-cli.db'))


def resolve(home=None, env=None, windows=None):
    env = os.environ if env is None else env
    windows = os.name == 'nt' if windows is None else windows
    home = Path(home or (env.get('USERPROFILE') if windows else env.get('HOME')) or Path.home()).resolve()
    native = home / '.claude-science'
    roots = [native]
    if windows:
        for base in (env.get('APPDATA', home / 'AppData/Roaming'), env.get('LOCALAPPDATA', home / 'AppData/Local')):
            roots.extend(Path(base) / name for name in ('ClaudeScience', 'Claude Science', 'claude-science'))
    roots = list(dict.fromkeys(p.resolve() for p in roots))
    config = expand(env['CSSWITCH_SCIENCE_CONFIG'], home, env) if env.get('CSSWITCH_SCIENCE_CONFIG') else native / 'config.toml'
    if env.get('CSSWITCH_SCIENCE_CONFIG') and not config.is_file():
        raise ValueError('指定的 Science 配置文件不存在，请检查 CSSWITCH_SCIENCE_CONFIG。')
    if not config.exists() and not env.get('CSSWITCH_SCIENCE_CONFIG'):
        configs = [p / 'config.toml' for p in roots if (p / 'config.toml').is_file()]
        if len(configs) > 1: raise ValueError('发现多个 Science 设置目录，请设置 CSSWITCH_SCIENCE_CONFIG 选择当前实例。')
        if configs: config = configs[0]
    settings = tomllib.loads(config.read_text(encoding='utf-8-sig')) if config.is_file() else {}
    if settings.get('data_dir_from'): raise ValueError('Science 正在迁移数据目录，请完成迁移后再切换。')
    explicit = env.get('CSSWITCH_SCIENCE_DATA_DIR') or settings.get('data_dir')
    if explicit:
        data = expand(explicit, home, env)
    else:
        found = [p for p in roots if has_history(p)]
        if len(found) > 1: raise ValueError('发现多个 Science 对话目录，请设置 CSSWITCH_SCIENCE_DATA_DIR 选择需要使用的目录。')
        data = found[0] if found else config.parent
    auth = expand(settings.get('paths', {}).get('auth_dir', str(config.parent)), home, env)
    if not config.is_file() and not (auth / 'encryption.key').exists() and (data / 'encryption.key').exists(): auth = data
    local = Path(env.get('LOCALAPPDATA', home / 'AppData/Local'))
    binary_candidates = ([native / 'bin/claude-science.exe', local / 'Programs/ClaudeScience/claude-science.exe', local / 'ClaudeScience/claude-science.exe'] if windows else
                         [native / 'bin/claude-science', Path('/Applications/Claude Science.app/Contents/Resources/bin/claude-science')])
    binary = expand(env['CSSWITCH_SCIENCE_BIN'], home, env) if env.get('CSSWITCH_SCIENCE_BIN') else next((p for p in binary_candidates if p.is_file()), binary_candidates[0])
    return dict(home=str(home), data=str(data), auth=str(auth), config=str(config), binary=str(binary), history_found=has_history(data))


if __name__ == '__main__':
    try:
        request = json.load(sys.stdin)
        print(json.dumps({'ok': True, 'value': resolve(request.get('home'))}, ensure_ascii=False))
    except (OSError, ValueError) as error:
        print(json.dumps({'ok': False, 'message': str(error)}, ensure_ascii=False))
