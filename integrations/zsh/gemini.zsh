# Native Antigravity shortcuts. Preserve the native CLI's permission-bypass behavior.
unalias gemini 2>/dev/null || true
gemini() {
  case ${1-} in
    -n|--name)
      shift
      local -a name_words=()
      while (( $# )) && [[ $1 != -* ]]; do
        name_words+=("$1")
        shift
      done
      local session_name="${(j: :)name_words}"
      if [[ -z ${session_name//[[:space:]]/} || $session_name == *$'\n'* || $session_name == *$'\r'* ]]; then
        print -u2 -- 'gemini: -n/--name requires a single-line session name before the next option'
        return 2
      fi
      print -u2 -- 'gemini: the installed Antigravity CLI has no native startup naming flag.'
      print -u2 -- "Start gemini, then enter this local command: /rename $session_name"
      return 2
      ;;
    -r|--resume)
      shift
      local -a resume_words=()
      while (( $# )) && [[ $1 != -* ]]; do
        resume_words+=("$1")
        shift
      done
      if (( ${#resume_words} )); then
        local session_id="${(j: :)resume_words}"
        if [[ ! $session_id =~ '^[[:xdigit:]]{8}-[[:xdigit:]]{4}-[[:xdigit:]]{4}-[[:xdigit:]]{4}-[[:xdigit:]]{12}$' ]]; then
          session_id=$(command env PYTHONPATH=/Users/danny/Documents/Tools/NativePeers /Users/danny/Documents/Tools/NativePeers/.venv/bin/python -P -m nativepeers.antigravity resolve-name "$session_id") || return $?
        fi
        set -- --conversation "$session_id" "$@"
      else
        print -u2 -- 'gemini: enter /resume in the native TUI to open its history picker.'
      fi
      ;;
    -c)
      shift
      set -- --continue "$@"
      ;;
  esac
  command env -u NPEER_BINDING -u NPEER_BOOTSTRAP -u CLAUDE_ENV_FILE -u CODEX_THREAD_ID agy --dangerously-skip-permissions "$@"
}
