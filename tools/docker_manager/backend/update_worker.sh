#!/usr/bin/env bash
# A transient user service owns this operation, including rollback.
set -u
umask 077
cd -- "$1" || exit 1
source ./plan.sh || exit 1
exec >/dev/null 2>&1

# timeout needs an executable, so resolve the Docker CLI before calling it.
docker() { timeout -k 5s "${DOCKER_TIMEOUT:-90}s" "$docker_cli" "$@"; }
docker_cli=$(type -P docker)

write_file() {
    printf '%s\n' "$2" > "$1.tmp" && mv -f -- "$1.tmp" "$1"
}

finish() {
    local status=$1 stage=$2 ids=${3:-}
    write_file result "$status $stage${ids:+ $ids}" || exit 1
    if [[ "$status" != needs_review ]]; then
        rm -f -- plan.sh compose.yaml next.json previous.json old-targets mounts-*
    fi
    exit 0
}

inspect() { docker container inspect --format "$1" "$2"; }

mounts() {
    inspect '{{range .Mounts}}{{if or (eq .Type "bind") (eq .Type "volume")}}{{printf "%q %q %q %q %t\n" .Type .Source .Name .Destination .RW}}{{end}}{{end}}' "$1" | sort
    return "${PIPESTATUS[0]}"
}

compose() {
    docker compose --env-file /dev/null --project-directory "$PWD" \
        -p "$project" -f compose.yaml -f "$1" "${@:2}"
}

compose_ids() {
    local id oneoff rows
    rows=$(docker ps -aq --no-trunc --filter "label=com.docker.compose.project=$project" \
        --filter "label=com.docker.compose.service=$service") || return 1
    for id in $rows; do
        oneoff=$(inspect '{{index .Config.Labels "com.docker.compose.oneoff"}}' "$id") || return 1
        case "$oneoff" in
            True|true|TRUE) ;;
            *) printf '%s\n' "$id" ;;
        esac
    done
}

current_ids() {
    if [[ "$mode" == run ]]; then
        inspect '{{.Id}}' "$target"
    else
        compose_ids | sort
        return "${PIPESTATUS[0]}"
    fi
}

old_target() {
    local id name restarts
    while read -r id name restarts; do
        if [[ "$name" == "$1" ]]; then
            printf '%s %s\n' "$id" "$restarts"
            return 0
        fi
    done < old-targets
    return 1
}

await_ready() {
    local image=$1 id state ids ready name before mount_map old_info old_restarts started=$SECONDS
    while (( SECONDS - started < 240 )); do
        ids=$(current_ids) || return 1
        local -a instances
        read -ra instances <<< "${ids//$'\n'/ }"
        (( ${#instances[@]} == ${#old_ids[@]} )) || return 1
        ready=true
        local -a pairs=()
        for id in "${instances[@]}"; do
            state=$(inspect '{{.Image}} {{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} {{.RestartCount}}' "$id") || return 1
            name=$(inspect '{{.Name}}' "$id") || return 1
            old_info=$(old_target "$name") || return 1
            read -r before old_restarts <<< "$old_info"
            mount_map=$(mounts "$id") || return 1
            [[ "$mount_map" == "$(<"mounts-$before")" ]] || return 1
            pairs+=("$before=$id")
            if [[ "$id" == "$before" && "$image" == "$old_image" ]]; then
                [[ "${state##* }" == "$old_restarts" ]] || return 1
                state="${state% *} 0"
            fi
            case "$state" in
                "$image running healthy 0") ;;
                "$image running starting 0") ready=false ;;
                "$image running none 0")
                    [[ "$self_update" == 1 ]] && return 1
                    (( SECONDS - started >= 30 )) || ready=false
                    ;;
                *) return 1 ;;
            esac
            if [[ "$self_update" == 1 && "$ready" == true ]]; then
                docker exec "$id" python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).close()" || ready=false
            fi
        done
        if [[ "$ready" == true ]]; then
            printf '%s\n' "${pairs[*]}"
            return 0
        fi
        sleep 2
    done
    return 1
}

rollback_run() {
    local old=${old_ids[0]} current owner
    current=$(inspect '{{.Id}}' "$target" 2>/dev/null) || current=
    if [[ -n "$current" && "$current" != "$old" ]]; then
        owner=$(inspect '{{index .Config.Labels "com.pansis-toolbox.update-run"}}' "$current") || return 1
        [[ "$owner" == "$run_id" ]] && docker rm -f "$current" || return 1
    fi
    if [[ "$current" != "$old" ]]; then
        docker rename "$old" "$target" || return 1
    fi
    if [[ $(inspect '{{.State.Running}}' "$old") != true ]]; then
        docker start "$old" || return 1
    fi
}

rollback() {
    local ids
    write_file state rolling_back || finish needs_review state_write
    if [[ "$mode" == run ]]; then
        rollback_run || finish needs_review rollback_failed
    else
        compose previous.json up -d --no-deps --no-build --pull never --force-recreate \
            --scale "$service=${#old_ids[@]}" "$service" || finish needs_review rollback_failed
    fi
    ids=$(await_ready "$old_image") || finish needs_review rollback_health
    if [[ "$mode" == compose ]]; then
        docker image rm "$backup" >/dev/null 2>&1 || true
    fi
    finish rolled_back "$1" "$ids"
}

exec 9>"../$target_lock.lock" || finish failed lock_unavailable
flock -n 9 || finish failed target_busy
if [[ -e result || -e state ]]; then exit 0; fi
write_file state accepted || exit 1
[[ -n "$docker_cli" && $(docker info --format '{{.ID}}') == "$engine_id" ]] || finish failed docker_context
ids=$(current_ids) || finish failed target_changed
[[ "${ids//$'\n'/ }" == "${old_ids[*]}" ]] || finish failed target_changed
for id in "${old_ids[@]}"; do
    [[ $(inspect '{{.Image}} {{.State.Status}}' "$id") == "$old_image running" ]] || finish failed target_changed
done
DOCKER_TIMEOUT=600 docker pull --platform "$platform" "$candidate" || finish failed pull_failed
[[ $(docker image inspect --format '{{.Id}}' "$candidate") == "$new_image" ]] || finish failed image_mismatch
IFS=/ read -r os arch _ <<< "$platform"
[[ $(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$candidate") == "$os/$arch" ]] || finish failed image_mismatch
ids=$(current_ids) || finish failed target_changed
[[ "${ids//$'\n'/ }" == "${old_ids[*]}" ]] || finish failed target_changed
for id in "${old_ids[@]}"; do
    [[ $(inspect '{{.Image}} {{.State.Status}}' "$id") == "$old_image running" ]] || finish failed target_changed
done
touch old-targets || finish failed state_write
for id in "${old_ids[@]}"; do
    name=$(inspect '{{.Name}}' "$id") || finish failed target_changed
    mounts "$id" > "mounts-$id" || finish failed target_changed
    restarts=$(inspect '{{.RestartCount}}' "$id") || finish failed target_changed
    printf '%s %s %s\n' "$id" "$name" "$restarts" >> old-targets || finish failed state_write
done

if [[ "$mode" == compose ]]; then
    docker image tag "$old_image" "$backup" || finish failed backup_failed
fi
write_file state replacing || finish failed state_write
if [[ "$mode" == run ]]; then
    docker stop "${old_ids[0]}" || rollback stop_failed
    docker rename "${old_ids[0]}" "$backup" || rollback rename_failed
    launch_run || rollback start_failed
else
    compose next.json up -d --no-deps --no-build --pull never --force-recreate \
        --scale "$service=${#old_ids[@]}" "$service" || rollback start_failed
fi
ids=$(await_ready "$new_image") || rollback unhealthy
if [[ "$mode" == run ]]; then
    docker rm "${old_ids[0]}" || finish succeeded cleanup_failed "$ids"
else
    docker image rm "$backup" || finish succeeded cleanup_failed "$ids"
fi
finish succeeded verified "$ids"
