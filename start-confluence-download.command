#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

say() { printf '\n%s\n' "$*"; }
fail() { say "错误：$*" >&2; exit 1; }

ensure_rust() {
  if ! command -v cargo >/dev/null 2>&1; then
    say "正在安装 Rust 工具链（首次运行需要联网，可能需要几分钟）……"
    command -v curl >/dev/null 2>&1 || fail "找不到 curl，无法安装 Rust。"
    local installer
    installer="$(mktemp -t rustup-init.XXXXXX)"
    trap 'rm -f "$installer"' RETURN
    curl --fail --location --silent --show-error https://sh.rustup.rs -o "$installer" || fail "下载 Rust 安装程序失败，请检查网络后重试。"
    RUSTUP_DIST_SERVER="https://rsproxy.cn" \
      RUSTUP_UPDATE_ROOT="https://rsproxy.cn/rustup" \
      sh "$installer" -y --profile minimal --default-toolchain stable || fail "Rust 安装失败。"
    export PATH="$HOME/.cargo/bin:$PATH"
  fi
  command -v cargo >/dev/null 2>&1 || fail "Rust 已安装，但 cargo 不在 PATH 中。"
  command -v rustup >/dev/null 2>&1 || fail "需要通过 rustup 管理 Rust 工具链。"
  if ! rustup toolchain list 2>/dev/null | grep -q '^stable-'; then
    say "正在安装 Rust stable 工具链……"
    RUSTUP_DIST_SERVER="https://rsproxy.cn" RUSTUP_UPDATE_ROOT="https://rsproxy.cn/rustup" \
      rustup toolchain install stable --profile minimal || fail "Rust stable 安装失败。"
  fi
}

build_app() {
  ensure_rust
  say "正在构建 confluence-dl……"
  RUSTUP_TOOLCHAIN=stable cargo +stable \
    --config 'source.crates-io.replace-with="rsproxy"' \
    --config 'source.rsproxy.registry="sparse+https://rsproxy.cn/index/"' \
    build --release || fail "构建失败。请确认网络可访问 Rust crates，并已安装 Xcode Command Line Tools（可运行 xcode-select --install）。"
  [[ -x "$ROOT/target/release/confluence-dl" ]] || fail "构建完成但未找到可执行文件。"
}

build_app

say "Confluence 文档下载"
read -r -p "页面 URL 或页面 ID：" page
[[ -n "$page" ]] || fail "页面 URL 或页面 ID 不能为空。"
read -r -p "Confluence 站点地址（例如 https://example.atlassian.net）：" site
[[ -n "$site" ]] || fail "站点地址不能为空。"
read -r -p "Atlassian 登录邮箱：" user
[[ -n "$user" ]] || fail "登录邮箱不能为空。"
read -r -s -p "Atlassian API Token（输入不显示）：" token
printf '\n'
[[ -n "$token" ]] || fail "API Token 不能为空。"
children="y"
read -r -p "下载附件？[y/N]：" attachments
read -r -p "输出目录 [桌面/Confluence Export]：" output
output="${output:-$HOME/Desktop/Confluence Export}"

export CONFLUENCE_URL="$site"
export CONFLUENCE_USER="$user"
export CONFLUENCE_TOKEN="$token"
unset token
args=("$page" --output "$output" --overwrite --children)
[[ "$attachments" =~ ^[Yy]$ ]] && args+=(--attachments)

say "开始下载到：$output"
if "$ROOT/target/release/confluence-dl" "${args[@]}"; then
  status=0
else
  status=$?
fi
unset CONFLUENCE_TOKEN
if [[ $status -eq 0 ]]; then
  say "下载完成。"
  open "$output" 2>/dev/null || true
else
  say "下载失败（退出码 $status）。请检查站点地址、页面权限和 API Token。"
fi
read -r -p "按回车键关闭窗口……" _
exit "$status"
