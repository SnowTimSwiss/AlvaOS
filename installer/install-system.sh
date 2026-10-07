#!/bin/bash
set -eE
set -o pipefail

# AlvaOS Installation Script 0.4.0
# This script installs AlvaOS to the target system with whiptail TUI and Mirror support

# Colors for terminal output (still useful for logs)
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Progress tracking
PROGRESS=0
TOTAL_STEPS=16

# Log file for output redirection
INSTALL_LOG="/tmp/alvaos-install.log"
touch "$INSTALL_LOG"

# GUI Helpers
msg() {
    whiptail --title "AlvaOS Installer" --msgbox "$1" 12 70
}

confirm() {
    whiptail --title "AlvaOS Installer" --yesno "$1" 12 70
}

input() {
    whiptail --title "AlvaOS Installer" --inputbox "$1" 12 70 "$2" 3>&1 1>&2 2>&3
}

# Validate IPv4 address format
validate_ip() {
    local ip="$1"
    local IFS='.'
    local -a octets
    read -ra octets <<< "$ip"
    
    # Must have exactly 4 octets
    if [[ ${#octets[@]} -ne 4 ]]; then
        return 1
    fi
    
    for octet in "${octets[@]}"; do
        # Must be a number
        if ! [[ "$octet" =~ ^[0-9]+$ ]]; then
            return 1
        fi
        # Must be 0-255
        if [[ "$octet" -lt 0 || "$octet" -gt 255 ]]; then
            return 1
        fi
    done
    return 0
}

# Validate netmask
validate_netmask() {
    local mask="$1"
    local valid_masks=("255.255.255.255" "255.255.255.254" "255.255.255.252" "255.255.255.248" "255.255.255.240" "255.255.255.224" "255.255.255.192" "255.255.255.128" "255.255.255.0" "255.255.254.0" "255.255.252.0" "255.255.248.0" "255.255.240.0" "255.255.224.0" "255.255.192.0" "255.255.128.0" "255.255.0.0" "255.254.0.0" "255.252.0.0" "255.248.0.0" "255.240.0.0" "255.224.0.0" "255.192.0.0" "255.128.0.0" "255.0.0.0" "254.0.0.0" "252.0.0.0" "248.0.0.0" "240.0.0.0" "224.0.0.0" "192.0.0.0" "128.0.0.0" "0.0.0.0")
    for valid in "${valid_masks[@]}"; do
        if [[ "$mask" == "$valid" ]]; then
            return 0
        fi
    done
    return 1
}

# Get validated IP input with retry.
# Return codes: 0 = got a valid value (printed to stdout), 2 = user cancelled/escaped (caller should go back a step)
get_validated_ip() {
    local prompt="$1"
    local default="$2"
    local max_attempts=3
    local attempt=0
    local ip

    while [[ $attempt -lt $max_attempts ]]; do
        if ! ip=$(input "$prompt" "$default"); then
            return 2
        fi
        if [[ -z "$ip" ]]; then
            msg "IP address cannot be empty. Please try again."
            attempt=$((attempt + 1))
            continue
        fi
        if validate_ip "$ip"; then
            echo "$ip"
            return 0
        else
            msg "Invalid IP address format: $ip\n\nPlease enter a valid IPv4 address (e.g., 192.168.1.50)"
            attempt=$((attempt + 1))
        fi
    done

    msg "Too many invalid attempts. Using default: $default"
    echo "$default"
    return 0
}

# Get validated netmask with retry.
# Return codes: 0 = got a valid value (printed to stdout), 2 = user cancelled/escaped (caller should go back a step)
get_validated_netmask() {
    local prompt="$1"
    local default="$2"
    local max_attempts=3
    local attempt=0
    local mask

    while [[ $attempt -lt $max_attempts ]]; do
        if ! mask=$(input "$prompt" "$default"); then
            return 2
        fi
        if [[ -z "$mask" ]]; then
            msg "Netmask cannot be empty. Please try again."
            attempt=$((attempt + 1))
            continue
        fi
        if validate_netmask "$mask"; then
            echo "$mask"
            return 0
        else
            msg "Invalid netmask: $mask\n\nPlease enter a valid netmask (e.g., 255.255.255.0)"
            attempt=$((attempt + 1))
        fi
    done

    msg "Too many invalid attempts. Using default: $default"
    echo "$default"
    return 0
}

menu() {
    title=$1
    shift
    whiptail --title "AlvaOS Installer" --menu "$title" 16 70 5 "$@" 3>&1 1>&2 2>&3
}

checklist() {
    title=$1
    shift
    whiptail --title "AlvaOS Installer" --checklist "$title" 16 70 5 "$@" 3>&1 1>&2 2>&3
}

update_progress() {
    step_msg=$1
    PROGRESS=$((PROGRESS + 1))
    PERCENT=$((PROGRESS * 100 / TOTAL_STEPS))
    echo "XXX"
    echo "$PERCENT"
    echo "$step_msg"
    echo "XXX"
    echo "[$(date +%T)] Step $PROGRESS/$TOTAL_STEPS: $step_msg" >> "$INSTALL_LOG"
}

netmask_to_prefix() {
    local mask="$1"
    local IFS=.
    local octets=($mask)
    local prefix=0
    for o in "${octets[@]}"; do
        case "$o" in
            255) prefix=$((prefix + 8)) ;;
            254) prefix=$((prefix + 7)) ;;
            252) prefix=$((prefix + 6)) ;;
            248) prefix=$((prefix + 5)) ;;
            240) prefix=$((prefix + 4)) ;;
            224) prefix=$((prefix + 3)) ;;
            192) prefix=$((prefix + 2)) ;;
            128) prefix=$((prefix + 1)) ;;
            0) ;;
            *) echo ""; return ;;
        esac
    done
    echo "$prefix"
}

wait_for_network() {
    local timeout_seconds="${1:-90}"
    local elapsed=0

    while [ "$elapsed" -lt "$timeout_seconds" ]; do
        if ip route | grep -q '^default' && getent hosts deb.debian.org >/dev/null 2>&1; then
            return 0
        fi
        sleep 2
        elapsed=$((elapsed + 2))
    done

    return 1
}

decode_plan_field() {
    local raw="${1:-}"
    printf '%b' "${raw//%/\\x}"
}

json_escape() {
    local text="${1:-}"
    text="${text//\\/\\\\}"
    text="${text//\"/\\\"}"
    text="${text//$'\n'/ }"
    text="${text//$'\r'/ }"
    printf '%s' "$text"
}

restore_pools_from_manifest() {
    local manifest_path="$1"
    local root_part="$2"
    local restore_root="/mnt/alvaos-restore"

    if [ ! -f "$manifest_path" ]; then
        msg "Manifest not found:\n$manifest_path"
        return 1
    fi

    declare -A POOL_NAME
    declare -A POOL_RAID
    declare -A POOL_MOUNT
    declare -A POOL_DEVCOUNT
    declare -A POOL_SELECTED
    declare -A POOL_NEW_ID
    declare -a POOL_IDS
    declare -a SNAPSHOT_ROWS

    while IFS='|' read -r rec f1 f2 f3 f4 f5 f6; do
        [ -z "$rec" ] && continue
        case "$rec" in
            \#*) continue ;;
            POOL)
                local pid pname raid mountp devcount
                pid=$(decode_plan_field "$f1")
                pname=$(decode_plan_field "$f2")
                raid=$(decode_plan_field "$f3")
                mountp=$(decode_plan_field "$f4")
                devcount=$(decode_plan_field "$f5")
                [ -z "$pid" ] && continue
                if [ -z "${POOL_NAME[$pid]+x}" ]; then
                    POOL_IDS+=("$pid")
                fi
                POOL_NAME["$pid"]="$pname"
                POOL_RAID["$pid"]="${raid:-single}"
                POOL_MOUNT["$pid"]="$mountp"
                if [[ "$devcount" =~ ^[0-9]+$ ]] && [ "$devcount" -gt 0 ]; then
                    POOL_DEVCOUNT["$pid"]="$devcount"
                else
                    POOL_DEVCOUNT["$pid"]=1
                fi
                ;;
            SNAPSHOT)
                SNAPSHOT_ROWS+=("$f1|$f2|$f3|$f4|$f5")
                ;;
        esac
    done < "$manifest_path"

    if [ "${#POOL_IDS[@]}" -eq 0 ]; then
        msg "The selected full-backup manifest has no pool entries."
        return 0
    fi

    local root_disk
    root_disk=$(lsblk -no PKNAME "$root_part" 2>/dev/null | head -n1 || true)

    declare -a ALL_DISKS
    while IFS='|' read -r dev size; do
        [ -z "$dev" ] && continue
        if [ -n "$root_disk" ] && [ "$dev" = "$root_disk" ]; then
            continue
        fi
        ALL_DISKS+=("$dev|$size")
    done < <(lsblk -dn -o NAME,SIZE,TYPE | awk '$3=="disk" {print $1 "|" $2}')

    if [ "${#ALL_DISKS[@]}" -eq 0 ]; then
        msg "No suitable disks available for pool restore."
        return 1
    fi

    local used_disks=""
    local pid
    for pid in "${POOL_IDS[@]}"; do
        local req pname raid opts item dev size selected selected_clean selected_count
        req="${POOL_DEVCOUNT[$pid]}"
        pname="${POOL_NAME[$pid]}"
        raid="${POOL_RAID[$pid]}"
        opts=()

        for item in "${ALL_DISKS[@]}"; do
            dev="${item%%|*}"
            size="${item#*|}"
            case " $used_disks " in
                *" $dev "*) continue ;;
            esac
            opts+=("$dev" "$size" "off")
        done

        if [ "${#opts[@]}" -eq 0 ]; then
            msg "Not enough free disks left for pool \"$pname\"."
            return 1
        fi

        if ! selected=$(checklist "Select exactly $req disk(s) for pool \"$pname\" (RAID: $raid)" "${opts[@]}"); then
            return 1
        fi
        selected_clean=$(echo "$selected" | tr -d '"')
        selected_count=$(echo "$selected_clean" | wc -w)
        if [ "$selected_count" -ne "$req" ]; then
            msg "Pool \"$pname\" requires exactly $req disk(s). You selected $selected_count."
            return 1
        fi

        POOL_SELECTED["$pid"]="$selected_clean"
        used_disks="$used_disks $selected_clean"
    done

    local summary="The following disks will be erased:\n"
    for pid in "${POOL_IDS[@]}"; do
        summary="$summary\n- ${POOL_NAME[$pid]} (${POOL_RAID[$pid]}): ${POOL_SELECTED[$pid]}"
    done
    if ! confirm "WARNING: $summary\n\nContinue with pool restore?"; then
        return 1
    fi

    mkdir -p "$restore_root"

    for pid in "${POOL_IDS[@]}"; do
        local pname raid mountp selected_list
        local -a selected_arr mkfs_cmd
        local pool_mount_dir

        pname="${POOL_NAME[$pid]}"
        raid="${POOL_RAID[$pid]}"
        mountp="${POOL_MOUNT[$pid]}"
        selected_list="${POOL_SELECTED[$pid]}"
        selected_arr=($selected_list)

        if [ "${#selected_arr[@]}" -eq 0 ]; then
            msg "Internal error: no disks selected for pool \"$pname\"."
            return 1
        fi

        mkfs_cmd=(mkfs.btrfs -f -L "$pname")
        if [ "$raid" != "single" ] && [ "${#selected_arr[@]}" -gt 1 ]; then
            mkfs_cmd+=(-d "$raid" -m "$raid")
        fi
        local d
        for d in "${selected_arr[@]}"; do
            mkfs_cmd+=("/dev/$d")
        done

        if ! "${mkfs_cmd[@]}" >> "$INSTALL_LOG" 2>&1; then
            msg "Failed to create pool filesystem for \"$pname\".\nSee: $INSTALL_LOG"
            return 1
        fi

        local new_uuid
        new_uuid=$(blkid -s UUID -o value "/dev/${selected_arr[0]}" 2>/dev/null | head -n1 || true)
        if [ -z "$new_uuid" ]; then
            new_uuid="$pid"
            echo "[WARN] Could not read new UUID for restored pool $pname; using manifest pool ID fallback." >> "$INSTALL_LOG"
        fi
        POOL_NEW_ID["$pid"]="$new_uuid"

        pool_mount_dir="$restore_root/$pid"
        mkdir -p "$pool_mount_dir"
        if ! mount "/dev/${selected_arr[0]}" "$pool_mount_dir" >> "$INSTALL_LOG" 2>&1; then
            msg "Failed to mount restored pool \"$pname\"."
            return 1
        fi

        local row rp rk source_path snapshot_path source_rel target_parent snapshot_on_installer received_path final_path
        for row in "${SNAPSHOT_ROWS[@]}"; do
            IFS='|' read -r rp rk source_path snapshot_path _ <<< "$row"
            rp=$(decode_plan_field "$rp")
            [ "$rp" != "$pid" ] && continue

            source_path=$(decode_plan_field "$source_path")
            snapshot_path=$(decode_plan_field "$snapshot_path")
            [ -z "$snapshot_path" ] && continue
            snapshot_on_installer="/mnt${snapshot_path}"
            if [ ! -d "$snapshot_on_installer" ]; then
                echo "[WARN] Snapshot path not found: $snapshot_on_installer" >> "$INSTALL_LOG"
                continue
            fi

            source_rel=""
            if [ -n "$mountp" ] && [ "$source_path" = "$mountp" ]; then
                source_rel=""
            elif [ -n "$mountp" ] && [[ "$source_path" == "$mountp/"* ]]; then
                source_rel="${source_path#"$mountp"/}"
            else
                source_rel="$(basename "$source_path")"
            fi
            if [ -z "$source_rel" ] || [ "$source_rel" = "." ]; then
                source_rel="pool-root"
            fi

            target_parent="$pool_mount_dir/$(dirname "$source_rel")"
            mkdir -p "$target_parent"

            if ! btrfs send "$snapshot_on_installer" | btrfs receive "$target_parent" >> "$INSTALL_LOG" 2>&1; then
                msg "Failed to restore snapshot $snapshot_path to pool \"$pname\".\nSee: $INSTALL_LOG"
                return 1
            fi

            received_path="$target_parent/$(basename "$snapshot_on_installer")"
            final_path="$pool_mount_dir/$source_rel"
            if [ "$received_path" != "$final_path" ]; then
                mkdir -p "$(dirname "$final_path")"
                if [ -e "$final_path" ]; then
                    if btrfs subvolume show "$final_path" >/dev/null 2>&1; then
                        btrfs subvolume delete "$final_path" >> "$INSTALL_LOG" 2>&1 || true
                    else
                        rm -rf "$final_path" >> "$INSTALL_LOG" 2>&1 || true
                    fi
                fi
                mv "$received_path" "$final_path" >> "$INSTALL_LOG" 2>&1
            fi
        done

        umount "$pool_mount_dir" >> "$INSTALL_LOG" 2>&1 || true
    done

    mkdir -p /mnt/var/lib/alvaos
    {
        echo "{"
        local idx=0
        local total="${#POOL_IDS[@]}"
        for pid in "${POOL_IDS[@]}"; do
            idx=$((idx + 1))
            local pname raid mountp selected_list pool_state_id
            local -a selected_arr
            pname="${POOL_NAME[$pid]}"
            raid="${POOL_RAID[$pid]}"
            mountp="${POOL_MOUNT[$pid]}"
            [ -n "$mountp" ] || mountp="/mnt/alvaos/${pname}"
            selected_list="${POOL_SELECTED[$pid]}"
            selected_arr=($selected_list)
            pool_state_id="${POOL_NEW_ID[$pid]}"
            [ -n "$pool_state_id" ] || pool_state_id="$pid"

            printf '  "%s": {\n' "$(json_escape "$pool_state_id")"
            printf '    "name": "%s",\n' "$(json_escape "$pname")"
            printf '    "devices": ['
            local first_dev=1
            local disk_name
            for disk_name in "${selected_arr[@]}"; do
                if [ "$first_dev" -eq 0 ]; then
                    printf ', '
                fi
                printf '"%s"' "$(json_escape "/dev/$disk_name")"
                first_dev=0
            done
            printf '],\n'
            printf '    "raid_level": "%s",\n' "$(json_escape "$raid")"
            printf '    "mount_point": "%s",\n' "$(json_escape "$mountp")"
            printf '    "created_at": "%s"\n' "$(date -Iseconds)"
            printf '  }'
            if [ "$idx" -lt "$total" ]; then
                printf ','
            fi
            printf '\n'
        done
        echo "}"
    } > /mnt/var/lib/alvaos/pools.json

    return 0
}

# Cleanup function for error recovery
cleanup() {
    local exit_code=$?
    if [ $exit_code -ne 0 ]; then
        msg "Installation failed! \n\nPlease check the logs at $INSTALL_LOG"
    fi
    
    # Unmount everything
    umount -l /mnt/sys 2>/dev/null || true
    umount -l /mnt/proc 2>/dev/null || true
    umount -l /mnt/dev/pts 2>/dev/null || true
    umount -l /mnt/dev 2>/dev/null || true
    umount -l /mnt/boot/efi 2>/dev/null || true
    umount -l /mnt 2>/dev/null || true
}

trap cleanup EXIT ERR INT TERM

rollback_from_snapshot() {
    local btrfs_entries=()
    while IFS='|' read -r dev size; do
        [ -z "$dev" ] && continue
        btrfs_entries+=("$dev" "$size")
    done < <(lsblk -ln -o NAME,SIZE,FSTYPE,TYPE | awk '$3=="btrfs" && $4=="part" {print "/dev/"$1 "|" $2}')

    if [ "${#btrfs_entries[@]}" -eq 0 ]; then
        msg "No Btrfs partitions found for rollback."
        return 1
    fi

    local root_part
    root_part=$(menu "Select Btrfs root partition for rollback" "${btrfs_entries[@]}")
    [ -z "$root_part" ] && return 1

    mkdir -p /mnt
    if ! mount -o subvolid=5 "$root_part" /mnt >> "$INSTALL_LOG" 2>&1; then
        msg "Failed to mount $root_part (subvolid=5)."
        return 1
    fi

    local snapshot_base="/mnt/var/lib/alvaos/system-snapshots"
    if [ ! -d "$snapshot_base" ]; then
        snapshot_base="/mnt/system-snapshots"
    fi
    if [ ! -d "$snapshot_base" ]; then
        msg "No system snapshot directory found.\n\nExpected:\n- /var/lib/alvaos/system-snapshots\n- /system-snapshots"
        return 1
    fi

    mapfile -t snapshot_names < <(find "$snapshot_base" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort -r)
    if [ "${#snapshot_names[@]}" -eq 0 ]; then
        msg "No snapshots found in $snapshot_base"
        return 1
    fi

    local snapshot_menu=()
    local snap
    for snap in "${snapshot_names[@]}"; do
        snapshot_menu+=("$snap" "System snapshot")
    done

    local selected
    selected=$(menu "Select snapshot to rollback to" "${snapshot_menu[@]}")
    [ -z "$selected" ] && return 1

    local snapshot_path="$snapshot_base/$selected"
    local subvol_id
    subvol_id=$(btrfs subvolume show "$snapshot_path" | awk -F': *' '/Subvolume ID:/ {print $2; exit}')
    if [ -z "$subvol_id" ]; then
        msg "Failed to read subvolume ID for snapshot:\n$snapshot_path"
        return 1
    fi

    local pool_restore_status="not_requested"
    local manifest_path="/mnt/var/lib/alvaos/full-system-manifests/${selected}.plan"
    if [ -f "$manifest_path" ]; then
        if confirm "Full backup manifest found for this snapshot.\n\nDo you also want to restore storage pools and data?"; then
            if restore_pools_from_manifest "$manifest_path" "$root_part"; then
                pool_restore_status="restored"
            else
                pool_restore_status="failed"
                if ! confirm "Pool restore failed.\n\nContinue with root rollback only?"; then
                    return 1
                fi
            fi
        fi
    fi

    if ! btrfs subvolume set-default "$subvol_id" /mnt >> "$INSTALL_LOG" 2>&1; then
        msg "Failed to set default subvolume to snapshot ID $subvol_id."
        return 1
    fi

    msg "Rollback prepared successfully.\n\nSnapshot: $selected\nSubvolume ID: $subvol_id\nPool restore: $pool_restore_status\n\nReboot now to boot into this snapshot."
    reboot
}

# Logo
LOGO="
╔═══════════════════════════════════════════════════════════╗
║                                                           ║
║       █████╗ ██╗    ██╗   ██╗ █████╗  ██████╗ ███████╗    ║
║      ██╔══██╗██║    ██║   ██║██╔══██╗██╔═══██╗██╔════╝    ║
║      ███████║██║    ██║   ██║███████║██║   ██║███████╗    ║
║      ██╔══██║██║    ╚██╗ ██╔╝██╔══██║██║   ██║╚════██║    ║
║      ██║  ██║███████╗╚████╔╝ ██║  ██║╚██████╔╝███████║    ║
║      ╚═╝  ╚═╝╚══════╝ ╚═══╝  ╚═╝  ╚═╝ ╚═════╝ ╚══════╝    ║
║                                                           ║
║            Your homelab journey starts here!              ║
║                                                           ║
╚═══════════════════════════════════════════════════════════╝"

whiptail --title "Welcome to AlvaOS" --msgbox "$LOGO" 20 70

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    msg "Please run as root (use sudo)"
    exit 1
fi

# Wizard steps. Cancel/Back/Escape in any dialog moves one step back instead of
# crashing the installer. Pressing Cancel/Escape on the very first step exits cleanly.
STEP=0
while [ "$STEP" -lt 6 ]; do
    case "$STEP" in
        0)
            # Action Selection
            if ACTION_MODE=$(menu "Select Action" \
                "INSTALL" "Install AlvaOS (default)" \
                "ROLLBACK" "Rollback system from snapshot"); then
                if [ "$ACTION_MODE" == "ROLLBACK" ]; then
                    rollback_from_snapshot
                    exit $?
                fi
                STEP=1
            else
                exit 0
            fi
            ;;
        1)
            # Mode Selection
            if INSTALL_MODE=$(menu "Select Installation Mode" \
                "SINGLE" "Install on a single disk (default)" \
                "MIRROR" "Mirror (RAID1) on two disks"); then
                STEP=2
            else
                STEP=0
            fi
            ;;
        2)
            # Disk Selection
            AVAILABLE_DISKS=$(lsblk -d -o NAME,SIZE,TYPE | grep disk | awk '{print $1 " (" $2 ") off"}')
            if [ "$INSTALL_MODE" == "SINGLE" ]; then
                if TARGET_DISKS=$(checklist "Select target disk" $AVAILABLE_DISKS); then
                    TOTAL_DISKS=$(echo $TARGET_DISKS | wc -w)
                    if [ "$TOTAL_DISKS" -ne 1 ]; then
                        msg "Please select exactly ONE disk for Single mode."
                        continue
                    fi
                    TARGET_DISKS=$(echo $TARGET_DISKS | tr -d '"')
                    STEP=3
                else
                    STEP=1
                fi
            else
                if TARGET_DISKS=$(checklist "Select TWO target disks for Mirror" $AVAILABLE_DISKS); then
                    TOTAL_DISKS=$(echo $TARGET_DISKS | wc -w)
                    if [ "$TOTAL_DISKS" -ne 2 ]; then
                        msg "Please select exactly TWO disks for Mirror mode."
                        continue
                    fi
                    TARGET_DISKS=$(echo $TARGET_DISKS | tr -d '"')
                    STEP=3
                else
                    STEP=1
                fi
            fi
            ;;
        3)
            # Confirm disk wipe
            if confirm "WARNING: This will ERASE ALL DATA on: $TARGET_DISKS\n\nAre you sure you want to continue?"; then
                STEP=4
            else
                STEP=2
            fi
            ;;
        4)
            # Network Selection
            if NET_MODE=$(menu "Network Configuration" \
                "DHCP" "Automatic, DHCP (default)" \
                "STATIC" "Manual (Static IP)"); then
                if [ "$NET_MODE" == "STATIC" ]; then
                    STEP=5
                else
                    STEP=6
                fi
            else
                STEP=3
            fi
            ;;
        5)
            # Static network details. Cancelling any field goes back to network mode selection.
            if ! STATIC_IP=$(get_validated_ip "Enter Static IP address (e.g., 192.168.1.50)" "192.168.1.50"); then
                STEP=4
                continue
            fi
            if ! STATIC_NETMASK=$(get_validated_netmask "Enter Netmask (e.g., 255.255.255.0)" "255.255.255.0"); then
                STEP=4
                continue
            fi
            if ! STATIC_GW=$(get_validated_ip "Enter Gateway (e.g., 192.168.1.1)" "192.168.1.1"); then
                STEP=4
                continue
            fi
            if ! STATIC_DNS=$(get_validated_ip "Enter DNS Server (e.g., 1.1.1.1)" "1.1.1.1"); then
                STEP=4
                continue
            fi
            STEP=6
            ;;
    esac
done

# Detected here (not inside the gauge pipe block below) so it survives into the final
# access-URL message: the gauge block runs in a subshell because it's piped to whiptail.
INTERFACE=$(ip -o link show | awk -F': ' '{print $2}' | grep -v lo | head -n1)
[ -z "$INTERFACE" ] && INTERFACE="eth0"

# Main Installation Logic wrapped in progress bar
{
    update_progress "Preparing disks..."
    for disk in $TARGET_DISKS; do
        disk_path="/dev/$disk"
        parted -s "$disk_path" mklabel gpt >> "$INSTALL_LOG" 2>&1
        parted -s "$disk_path" mkpart primary fat32 1MiB 512MiB >> "$INSTALL_LOG" 2>&1
        parted -s "$disk_path" set 1 esp on >> "$INSTALL_LOG" 2>&1
        parted -s "$disk_path" mkpart primary btrfs 512MiB 100% >> "$INSTALL_LOG" 2>&1
        partprobe "$disk_path" >> "$INSTALL_LOG" 2>&1 || true
    done
    sleep 2

    update_progress "Formatting partitions..."
    DISK_ARRAY=($TARGET_DISKS)
    if [ "$INSTALL_MODE" == "SINGLE" ]; then
        disk=${DISK_ARRAY[0]}
        if [[ "$disk" == *"nvme"* ]]; then
            EFI_PART="/dev/${disk}p1"
            ROOT_PART="/dev/${disk}p2"
        else
            EFI_PART="/dev/${disk}1"
            ROOT_PART="/dev/${disk}2"
        fi
        mkfs.fat -F32 "$EFI_PART" >> "$INSTALL_LOG" 2>&1
        mkfs.btrfs -f "$ROOT_PART" >> "$INSTALL_LOG" 2>&1
        mount "$ROOT_PART" /mnt >> "$INSTALL_LOG" 2>&1
        btrfs subvolume create /mnt/@ >> "$INSTALL_LOG" 2>&1
        btrfs subvolume create /mnt/system-snapshots >> "$INSTALL_LOG" 2>&1
        umount /mnt >> "$INSTALL_LOG" 2>&1
        mount -o subvol=@ "$ROOT_PART" /mnt >> "$INSTALL_LOG" 2>&1
    else
        # Mirror mode
        EFI_PARTS=()
        ROOT_PARTS=()
        for disk in "${DISK_ARRAY[@]}"; do
            if [[ "$disk" == *"nvme"* ]]; then
                EFI_PARTS+=("/dev/${disk}p1")
                ROOT_PARTS+=("/dev/${disk}p2")
            else
                EFI_PARTS+=("/dev/${disk}1")
                ROOT_PARTS+=("/dev/${disk}2")
            fi
        done
        
        for efi in "${EFI_PARTS[@]}"; do
            mkfs.fat -F32 "$efi" >> "$INSTALL_LOG" 2>&1
        done
        
        # Create Btrfs RAID1
        mkfs.btrfs -f -d raid1 -m raid1 "${ROOT_PARTS[@]}" >> "$INSTALL_LOG" 2>&1
        mount "${ROOT_PARTS[0]}" /mnt >> "$INSTALL_LOG" 2>&1

        # Safety net: verify the filesystem actually came up as raid1 data+metadata.
        # If mkfs ever fell back to "single" (e.g. older btrfs-progs ignoring -d/-m on
        # some block layouts), force a real conversion now rather than silently leaving
        # a 2-disk system install with no redundancy.
        usage_out=$(btrfs filesystem usage /mnt 2>>"$INSTALL_LOG")
        if ! echo "$usage_out" | grep -qi "RAID1"; then
            echo "[$(date +%T)] WARNING: btrfs did not report raid1 after mkfs, forcing balance convert to raid1." >> "$INSTALL_LOG"
            btrfs balance start -dconvert=raid1 -mconvert=raid1 -f /mnt >> "$INSTALL_LOG" 2>&1
        fi

        btrfs subvolume create /mnt/@ >> "$INSTALL_LOG" 2>&1
        btrfs subvolume create /mnt/system-snapshots >> "$INSTALL_LOG" 2>&1
        umount /mnt >> "$INSTALL_LOG" 2>&1
        mount -o subvol=@ "${ROOT_PARTS[0]}" /mnt >> "$INSTALL_LOG" 2>&1
        EFI_PART=${EFI_PARTS[0]} # Use first EFI partition for initial mount
    fi

    mkdir -p /mnt/boot/efi
    mount "$EFI_PART" /mnt/boot/efi >> "$INSTALL_LOG" 2>&1

    update_progress "Checking installer network..."
    if ! wait_for_network 90; then
        echo "[$(date +%T)] Network preflight failed: no default route or DNS resolution for deb.debian.org." >> "$INSTALL_LOG"
        exit 1
    fi

    update_progress "Installing base system (debootstrap)..."
    debootstrap --arch=amd64 trixie /mnt http://deb.debian.org/debian >> "$INSTALL_LOG" 2>&1

    update_progress "Configuring system..."
    echo "alvaos" > /mnt/etc/hostname
    cat > /mnt/etc/hosts << HOSTS_EOF
127.0.0.1   localhost
127.0.1.1   alvaos
::1         localhost ip6-localhost ip6-loopback
HOSTS_EOF

    update_progress "Configuring networking..."
    mkdir -p /mnt/etc/NetworkManager
    cat > /mnt/etc/NetworkManager/NetworkManager.conf << 'NMCONF_EOF'
[main]
plugins=ifupdown,keyfile

[ifupdown]
managed=true
NMCONF_EOF

    cat > /mnt/etc/network/interfaces << NET_EOF
auto lo
iface lo inet loopback
NET_EOF

    NM_CONN_DIR=/mnt/etc/NetworkManager/system-connections
    mkdir -p "$NM_CONN_DIR"
    chmod 700 "$NM_CONN_DIR"
    CONN_UUID=$(cat /proc/sys/kernel/random/uuid)

    if [ "$NET_MODE" == "STATIC" ]; then
        PREFIX=$(netmask_to_prefix "$STATIC_NETMASK")
        [ -z "$PREFIX" ] && PREFIX="24"
        cat > "$NM_CONN_DIR/alvaos.nmconnection" << NM_EOF
[connection]
id=alvaos
uuid=$CONN_UUID
type=ethernet
interface-name=$INTERFACE
autoconnect=true

[ipv4]
method=manual
addresses1=$STATIC_IP/$PREFIX,$STATIC_GW
dns=$STATIC_DNS;
dns-search=

[ipv6]
method=ignore
NM_EOF
    else
        cat > "$NM_CONN_DIR/alvaos.nmconnection" << NM_EOF
[connection]
id=alvaos
uuid=$CONN_UUID
type=ethernet
interface-name=$INTERFACE
autoconnect=true

[ipv4]
method=auto
dhcp-hostname=alvaos

[ipv6]
method=ignore
NM_EOF
    fi
    chmod 600 "$NM_CONN_DIR/alvaos.nmconnection"

    # Show the live IP address on the console login screen (every boot), not just a
    # one-time install-time snapshot. agetty resolves \4/\6 at the moment the login
    # prompt is rendered, so this always reflects the IP actually in use post-boot,
    # which matters most for DHCP where the installer-time IP can differ from later boots.
    if ! grep -q "AlvaOS network" /mnt/etc/issue 2>/dev/null; then
        {
            echo ""
            echo "AlvaOS network: \\4{$INTERFACE} (web UI: http://\\4{$INTERFACE}:8080)"
        } >> /mnt/etc/issue
    fi

    update_progress "Configuring apt sources..."
    cat > /mnt/etc/apt/sources.list << SOURCES_EOF
deb http://deb.debian.org/debian trixie main contrib non-free non-free-firmware
deb http://deb.debian.org/debian trixie-updates main contrib non-free non-free-firmware
deb http://security.debian.org/debian-security trixie-security main contrib non-free non-free-firmware
SOURCES_EOF

    update_progress "Configuring fstab..."
    # findmnt on btrfs returns e.g. "/dev/sda2[/@]" — strip bracket notation and take first line
    ROOT_SOURCE=$(findmnt -n -o SOURCE /mnt | head -n1 | sed 's/\[.*\]//')
    ROOT_UUID=""
    if [ -n "$ROOT_SOURCE" ]; then
        ROOT_UUID=$(blkid -s UUID -o value "$ROOT_SOURCE" 2>/dev/null | head -n1)
    fi
    # Fallback: if UUID is empty, use the device path directly
    if [ -n "$ROOT_UUID" ]; then
        ROOT_ID="UUID=$ROOT_UUID"
    else
        ROOT_ID="$ROOT_SOURCE"
        echo "[$(date +%T)] WARNING: Could not detect UUID for $ROOT_SOURCE, using device path" >> "$INSTALL_LOG"
    fi
    cat > /mnt/etc/fstab << FSTAB_EOF
$ROOT_ID  /          btrfs  defaults,subvol=@  0  1
$ROOT_ID  /var/lib/alvaos/system-snapshots  btrfs  defaults,subvol=system-snapshots  0  2
FSTAB_EOF
    
    # EFI partitions in fstab
    if [ "$INSTALL_MODE" == "SINGLE" ]; then
        BOOT_UUID=$(blkid -s UUID -o value "$EFI_PART")
        echo "UUID=$BOOT_UUID  /boot/efi  vfat  defaults  0  2" >> /mnt/etc/fstab
    else
        # Mirror: For simplicity, pick first EFI for /boot/efi auto-mount
        BOOT_UUID=$(blkid -s UUID -o value "${EFI_PARTS[0]}")
        echo "UUID=$BOOT_UUID  /boot/efi  vfat  defaults  0  2" >> /mnt/etc/fstab
    fi

    update_progress "Mounting virtual filesystems..."
    mount --rbind /dev /mnt/dev >> "$INSTALL_LOG" 2>&1
    mount --make-rslave /mnt/dev >> "$INSTALL_LOG" 2>&1
    mount --rbind /sys /mnt/sys >> "$INSTALL_LOG" 2>&1
    mount --make-rslave /mnt/sys >> "$INSTALL_LOG" 2>&1
    mount -t proc proc /mnt/proc >> "$INSTALL_LOG" 2>&1

    update_progress "Installing kernel and essential packages..."
    cp -L /etc/resolv.conf /mnt/etc/resolv.conf >> "$INSTALL_LOG" 2>&1 || true
    chroot /mnt apt-get -o Acquire::Retries=3 update >> "$INSTALL_LOG" 2>&1
    chroot /mnt env DEBIAN_FRONTEND=noninteractive apt-get install -y \
        -o Acquire::Retries=3 \
        -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" \
        linux-image-amd64 python3 python3-flask python3-waitress python3-psutil python3-requests python3-packaging python3-yaml python3-cryptography \
        python3-pyotp python3-qrcode python3-pil samba-vfs-modules miniupnpc \
        systemd systemd-timesyncd network-manager openssh-server docker.io docker-compose btrfs-progs wireguard-tools nbd-client cryptsetup \
        curl wget vim sudo smartmontools hdparm nfs-kernel-server samba pciutils iproute2 iputils-ping >> "$INSTALL_LOG" 2>&1

    update_progress "Installing bootloader..."
    if [ -d /sys/firmware/efi ]; then
        chroot /mnt env DEBIAN_FRONTEND=noninteractive apt-get install -y \
            -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" \
            grub-efi-amd64 efibootmgr >> "$INSTALL_LOG" 2>&1
        FIRST_EFI_DISK=1
        for disk in $TARGET_DISKS; do
            chroot /mnt grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=AlvaOS --recheck --removable >> "$INSTALL_LOG" 2>&1
            if [ "$FIRST_EFI_DISK" -eq 1 ]; then
                # Also register a real NVRAM boot entry (not just the removable fallback path),
                # so we can point the firmware at it directly below instead of relying on BootOrder.
                chroot /mnt grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=AlvaOS --recheck >> "$INSTALL_LOG" 2>&1 || true
                FIRST_EFI_DISK=0
            fi
        done

        # Force the very next boot to the installed system regardless of current BootOrder
        # (the USB installer is often listed first). This lets EFI users just reboot and
        # remove the stick whenever convenient instead of shutting down and restarting by hand.
        ALVAOS_BOOTNUM=$(chroot /mnt efibootmgr 2>/dev/null | grep -m1 "AlvaOS" | sed -n 's/^Boot\([0-9A-Fa-f]\{4\}\).*/\1/p')
        if [ -n "$ALVAOS_BOOTNUM" ] && chroot /mnt efibootmgr --bootnext "$ALVAOS_BOOTNUM" >> "$INSTALL_LOG" 2>&1; then
            touch /tmp/alvaos-bootnext-set
        fi
    else
        chroot /mnt env DEBIAN_FRONTEND=noninteractive apt-get install -y \
            -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" \
            grub-pc >> "$INSTALL_LOG" 2>&1
        for disk in $TARGET_DISKS; do
            chroot /mnt grub-install --target=i386-pc "/dev/$disk" >> "$INSTALL_LOG" 2>&1
        done
    fi
    chroot /mnt update-grub >> "$INSTALL_LOG" 2>&1
    chroot /mnt update-initramfs -u >> "$INSTALL_LOG" 2>&1

    update_progress "Securing root account..."
    RANDOM_PASS=$(head -c 32 /dev/urandom | base64 | tr -d '/+=' | head -c 24)
    echo "root:${RANDOM_PASS}" | chroot /mnt chpasswd >> "$INSTALL_LOG" 2>&1

    update_progress "Setting up AlvaOS components..."
    mkdir -p /mnt/opt/alvaos/{bin,webui} /mnt/etc/alvaos /mnt/var/lib/alvaos /mnt/var/lib/alvaos/system-snapshots /mnt/var/log/alvaos /mnt/opt/alvaos/scripts

    # Copy backend
    if [ -d "/opt/alvaos/backend" ]; then
        cp /opt/alvaos/backend/*.py /mnt/opt/alvaos/bin/ 2>/dev/null || true
        chmod +x /mnt/opt/alvaos/bin/*.py 2>/dev/null || true
        cp /opt/alvaos/backend/alvaos-priv /mnt/opt/alvaos/bin/alvaos-priv
        chmod 755 /mnt/opt/alvaos/bin/alvaos-priv
    fi
    if [ -f "/opt/alvaos/keys/update-signing.pub" ]; then
        mkdir -p /mnt/opt/alvaos/keys
        cp /opt/alvaos/keys/update-signing.pub /mnt/opt/alvaos/keys/update-signing.pub
    fi

    # Copy scripts
    if [ -d "/opt/alvaos/scripts" ]; then
        cp /opt/alvaos/scripts/update_checker.sh /mnt/opt/alvaos/scripts/ 2>/dev/null || true
        cp /opt/alvaos/scripts/apply_update.sh /mnt/opt/alvaos/scripts/ 2>/dev/null || true
        cp /opt/alvaos/scripts/setup_sudoers.sh /mnt/opt/alvaos/scripts/ 2>/dev/null || true
        cp /opt/alvaos/scripts/sudoers.alvaos /mnt/opt/alvaos/scripts/ 2>/dev/null || true
        chmod +x /mnt/opt/alvaos/scripts/*.sh 2>/dev/null || true
        for unit in alvaos-update-checker.service alvaos-update-checker.timer alvaos-watchdog.service alvaos-watchdog.timer alvaos-files.service alvaos-vm@.service; do
            [ -f "/opt/alvaos/scripts/${unit}" ] && cp "/opt/alvaos/scripts/${unit}" /mnt/etc/systemd/system/
        done
    fi

    # Copy frontend
    [ -d "/opt/alvaos/webui" ] && cp -r /opt/alvaos/webui/* /mnt/opt/alvaos/webui/
    [ -f "/opt/alvaos/VERSION" ] && cp /opt/alvaos/VERSION /mnt/etc/alvaos/VERSION
    if [ -d "/opt/alvaos/apps" ]; then
        mkdir -p /mnt/opt/alvaos/apps
        cp -r /opt/alvaos/apps/* /mnt/opt/alvaos/apps/ 2>/dev/null || true
    fi

    update_progress "Configuring services..."
    cat > /mnt/etc/systemd/system/alvaos.service << SERVICE_EOF
[Unit]
Description=AlvaOS Web Interface
After=network.target

[Service]
Type=simple
User=alvaos
Group=alvaos
WorkingDirectory=/opt/alvaos/bin
ExecStart=/usr/bin/python3 /opt/alvaos/bin/alvaos-backend.py
Restart=always
RestartSec=10
Environment="PYTHONUNBUFFERED=1"

[Install]
WantedBy=multi-user.target
SERVICE_EOF

    # Sudoers
    mkdir -p /mnt/etc/sudoers.d
    SUDOERS_SOURCE=""
    for candidate in \
        "/opt/alvaos/scripts/sudoers.alvaos" \
        "$SCRIPT_DIR/scripts/sudoers.alvaos" \
        "$SCRIPT_DIR/../scripts/sudoers.alvaos"; do
        if [ -f "$candidate" ]; then
            SUDOERS_SOURCE="$candidate"
            break
        fi
    done

    if [ -z "$SUDOERS_SOURCE" ]; then
        msg "Sudoers template not found (scripts/sudoers.alvaos)."
        exit 1
    fi

    cp "$SUDOERS_SOURCE" /mnt/etc/sudoers.d/alvaos
    chown root:root /mnt/etc/sudoers.d/alvaos
    chmod 440 /mnt/etc/sudoers.d/alvaos
    chroot /mnt visudo -c -f /etc/sudoers.d/alvaos >> "$INSTALL_LOG" 2>&1 || {
        msg "Sudoers validation failed for /etc/sudoers.d/alvaos"
        exit 1
    }

    # Load the WireGuard module on the host at boot so VPN apps work without
    # the SYS_MODULE capability (which would let a container load kernel code).
    mkdir -p /mnt/etc/modules-load.d
    echo wireguard > /mnt/etc/modules-load.d/alvaos-wireguard.conf
    # Buddy Backup attaches the encrypted vault on a buddy as a network block device.
    echo nbd > /mnt/etc/modules-load.d/alvaos-nbd.conf
    mkdir -p /mnt/etc/modprobe.d
    echo "options nbd nbds_max=16 max_part=0" > /mnt/etc/modprobe.d/alvaos-nbd.conf

    # SSH Security
    mkdir -p /mnt/etc/ssh/sshd_config.d
    cat > /mnt/etc/ssh/sshd_config.d/00-alvaos-security.conf << 'SSH_EOF'
PermitRootLogin no
PasswordAuthentication yes
PermitEmptyPasswords no
SSH_EOF

    update_progress "Finalizing configuration..."
    chroot /mnt useradd -r -s /usr/sbin/nologin -d /var/lib/alvaos -M alvaos >> "$INSTALL_LOG" 2>&1 || true
    # Virtual machines run as their own account (backend/vm_ops.py)
    chroot /mnt useradd -r -g alvaos -s /usr/sbin/nologin -d /nonexistent -M alvaos-vm >> "$INSTALL_LOG" 2>&1 || true
    mkdir -p /mnt/var/lib/alvaos/compose /mnt/var/lib/alvaos/updates
    # Code stays root-owned: parts of it (watchdog, update script, privilege
    # helper) run as root. Only state, logs and config belong to the service.
    chroot /mnt chown -R root:root /opt/alvaos >> "$INSTALL_LOG" 2>&1
    chroot /mnt chmod -R go-w /opt/alvaos >> "$INSTALL_LOG" 2>&1
    chroot /mnt chown -R alvaos:alvaos /var/lib/alvaos /var/log/alvaos /etc/alvaos >> "$INSTALL_LOG" 2>&1
    
    # Version file
    cat > /mnt/etc/alvaos/version.json << VERSION_EOF
{
  "alvaos_version": "0.4.0",
  "build_date": "$(date +%Y-%m-%d)",
  "installer_version": "0.4.0"
}
VERSION_EOF

    # Enable services
    chroot /mnt systemctl enable alvaos.service >> "$INSTALL_LOG" 2>&1 || true
    [ -f "/mnt/etc/systemd/system/alvaos-update-checker.timer" ] && chroot /mnt systemctl enable alvaos-update-checker.timer >> "$INSTALL_LOG" 2>&1 || true
    [ -f "/mnt/etc/systemd/system/alvaos-watchdog.timer" ] && chroot /mnt systemctl enable alvaos-watchdog.timer >> "$INSTALL_LOG" 2>&1 || true
    chroot /mnt systemctl enable NetworkManager >> "$INSTALL_LOG" 2>&1 || true

    update_progress "Cleanup..."
    chroot /mnt apt-get clean >> "$INSTALL_LOG" 2>&1
    umount -l /mnt/sys /mnt/proc /mnt/dev/pts /mnt/dev /mnt/boot/efi /mnt >> "$INSTALL_LOG" 2>&1 || true

    echo "100"
} | whiptail --title "Installing AlvaOS" --gauge "This usually takes 10-15 minutes depending on your hardware and internet speed. Please wait..." 10 70 0

# Final Message
if [ "$NET_MODE" == "STATIC" ]; then
    ACCESS_LINE="Access AlvaOS at: http://$STATIC_IP:8080"
else
    ACCESS_LINE="Network mode: DHCP. The IP address is assigned automatically by your router and is NOT fixed yet, so it will be shown on the console login screen after boot (and via 'ip a'). Visit http://<that-ip>:8080 to complete setup."
fi

if [ -f /tmp/alvaos-bootnext-set ]; then
    # EFI firmware was told to boot straight into AlvaOS next, regardless of BootOrder, so it's
    # safe to reboot immediately. The USB stick can be removed any time, before or after.
    msg "Installation Successful!\n\nPress Enter to reboot into AlvaOS. The USB installer stick can be removed at any time, but not before the system fully shut down.\n\n$ACCESS_LINE"
    reboot
else
    # Legacy BIOS (or EFI bootnext could not be set): we can't reliably control boot order from
    # software, so shut down instead of reboot to give an unambiguous point to pull the USB stick.
    msg "Installation Successful!\n\nPress Enter to shut down. Wait until the machine is completely off, then remove the USB installer stick before powering it back on.\n\n$ACCESS_LINE"
    shutdown -h now
fi
