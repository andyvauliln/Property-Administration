"""
Daily Manager Activity Report - Telegram Notification
Sends a daily summary of all database changes (create/update/delete) for core models to Telegram group.
"""
from datetime import timedelta, date
from django.utils import timezone
from mysite.models import AuditLog
from mysite.management.commands.base_command import BaseCommandWithErrorHandling
from mysite.unified_logger import log_info, log_error
import os
import requests


# Core models to track
TRACKED_MODELS = ['Payment', 'Booking', 'Cleaning', 'Apartment', 'User', 'ParkingBooking', 'HandyManBooking']

# Fields to ignore (auto-updated timestamps and tracking fields, not real changes)
IGNORED_FIELDS = [
    'updated_at', 'created_at', 'modified_at', 'last_modified', 'timestamp',
    'last_updated_by', 'last_updated_at', 'modified_by', 'created_by'
]

# Telegram message limit
MAX_MESSAGE_LENGTH = 4000  # Leave some buffer from 4096


def send_telegram_message(chat_id, token, message):
    """Send a message to Telegram"""
    if not token or not chat_id:
        return False
    
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML"
        }
        response = requests.post(url, data=data, timeout=10)
        response.raise_for_status()
        return True
    except Exception as e:
        log_error(e, context="telegram_manager_activity send_telegram_message")
        return False


def split_message(message, max_length=MAX_MESSAGE_LENGTH):
    """
    Split a long message into multiple messages that fit within Telegram's limit.
    Tries to split at newlines to keep formatting intact.
    """
    if len(message) <= max_length:
        return [message]
    
    messages = []
    current_message = ""
    
    lines = message.split('\n')
    for line in lines:
        # If adding this line would exceed the limit
        if len(current_message) + len(line) + 1 > max_length:
            if current_message:
                messages.append(current_message.strip())
                current_message = ""
            
            # If a single line is too long, split it
            if len(line) > max_length:
                while len(line) > max_length:
                    messages.append(line[:max_length])
                    line = line[max_length:]
                current_message = line + '\n'
            else:
                current_message = line + '\n'
        else:
            current_message += line + '\n'
    
    if current_message.strip():
        messages.append(current_message.strip())
    
    return messages


def format_value(value):
    """Format a value for display (no shortening; only trim whitespace)."""
    if value is None:
        return "None"
    # Keep full value; just trim edges and avoid multiline breaking formatting.
    value_str = str(value).strip()
    value_str = " ".join(value_str.splitlines()).strip()
    return value_str


def extract_apartment_name(log):
    """Extract apartment name from log data"""
    values = log.new_values if log.action in ['create', 'update'] else log.old_values
    if not values:
        return None
    
    # Direct apartment field
    if 'apartment' in values and values['apartment']:
        return str(values['apartment'])
    
    # For apartments model, use name field
    if log.model_name == 'Apartment' and 'name' in values:
        return str(values['name'])
    
    # Object repr often contains apartment info
    if log.object_repr:
        return str(log.object_repr).strip()
    
    return None


def extract_tenant_name(log):
    """Extract tenant name from log data"""
    values = log.new_values if log.action in ['create', 'update'] else log.old_values
    if not values:
        return None
    
    # Direct tenant field
    if 'tenant' in values and values['tenant']:
        return str(values['tenant'])
    
    # For User model, use full_name
    if log.model_name == 'User' and 'full_name' in values:
        return str(values['full_name'])
    
    return None


def format_change_detail(log):
    """Format a single audit log entry for display"""
    timestamp = log.timestamp.strftime("%H:%M")
    action_emoji = {
        'create': '➕',
        'update': '✏️',
        'delete': '🗑️'
    }.get(log.action, '•')
    
    # Build first line: [Action] [Model] | [Apartment] | [Tenant] | [Time]
    first_line_parts = [f"{action_emoji} <b>{log.action.upper()}</b> {log.model_name}"]
    
    # Add apartment name if available
    apartment = extract_apartment_name(log)
    if apartment:
        first_line_parts.append(apartment)
    
    # Add tenant name if available (for relevant models)
    if log.model_name in ['Booking', 'Payment', 'Cleaning', 'ParkingBooking', 'HandyManBooking']:
        tenant = extract_tenant_name(log)
        if tenant:
            first_line_parts.append(tenant)
    
    # Add time
    first_line_parts.append(timestamp)
    
    detail = "      " + " | ".join(first_line_parts)
    
    # Show field changes with old → new values (excluding auto-timestamp fields)
    if log.action == 'update' and log.changed_fields:
        real_fields = [f for f in log.changed_fields if f not in IGNORED_FIELDS]
        if real_fields:
            detail += f"\n         📝 <b>Changes:</b>"
            for field in real_fields[:15]:  # Show up to 15 fields
                old_val = log.old_values.get(field, 'N/A') if log.old_values else 'N/A'
                new_val = log.new_values.get(field, 'N/A') if log.new_values else 'N/A'
                detail += f"\n            • {field}: {format_value(old_val)} → {format_value(new_val)}"
            if len(real_fields) > 15:
                detail += f"\n            <i>... +{len(real_fields) - 15} more fields</i>"
    
    # Show new values for creates (all fields except auto-timestamps and IDs)
    elif log.action == 'create' and log.new_values:
        detail += f"\n         📝 <b>Values:</b>"
        # Fields to skip (auto-generated, not meaningful)
        skip_fields = IGNORED_FIELDS + ['id', 'pk']
        shown = 0
        for field, value in log.new_values.items():
            if field in skip_fields:
                continue
            # Skip None/empty values
            if value is None or value == '' or value == 'None':
                continue
            detail += f"\n            • {field}: {format_value(value)}"
            shown += 1
            if shown >= 15:  # Limit to prevent huge messages
                remaining = len([f for f in log.new_values if f not in skip_fields]) - shown
                if remaining > 0:
                    detail += f"\n            <i>... +{remaining} more fields</i>"
                break
    
    # Show old values for deletes (all fields except auto-timestamps and IDs)
    elif log.action == 'delete' and log.old_values:
        detail += f"\n         📝 <b>Deleted data:</b>"
        skip_fields = IGNORED_FIELDS + ['id', 'pk']
        shown = 0
        for field, value in log.old_values.items():
            if field in skip_fields:
                continue
            if value is None or value == '' or value == 'None':
                continue
            detail += f"\n            • {field}: {format_value(value)}"
            shown += 1
            if shown >= 15:
                remaining = len([f for f in log.old_values if f not in skip_fields]) - shown
                if remaining > 0:
                    detail += f"\n            <i>... +{remaining} more fields</i>"
                break
    
    return detail


def has_real_changes(log):
    """
    Check if a log entry has real changes (not just auto-updated timestamps).
    Returns True if there are meaningful field changes.
    """
    if log.action != 'update':
        return True  # Creates and deletes are always real changes
    
    if not log.changed_fields:
        return False
    
    # Filter out ignored fields
    real_fields = [f for f in log.changed_fields if f not in IGNORED_FIELDS]
    return len(real_fields) > 0


def get_real_changed_fields(log):
    """Get list of changed fields excluding ignored auto-update fields"""
    if not log.changed_fields:
        return []
    return [f for f in log.changed_fields if f not in IGNORED_FIELDS]


def generate_daily_report():
    """
    Generate the daily activity report from AuditLog.
    Returns a list of messages (split if needed).
    """
    # Get logs from the last 24 hours
    yesterday = timezone.now() - timedelta(hours=24)
    
    logs = AuditLog.objects.filter(
        timestamp__gte=yesterday,
        model_name__in=TRACKED_MODELS
    ).order_by('model_name', '-timestamp')
    
    if not logs.exists():
        return ["📊 <b>Daily Manager Activity Report</b>\n\nNo changes recorded in the last 24 hours for tracked models."]
    
    # Group logs by user first, then by model
    logs_by_user = {}
    summary = {'create': 0, 'update': 0, 'delete': 0}
    skipped_count = 0
    
    for log in logs:
        # Skip updates that only changed auto-timestamp fields
        if not has_real_changes(log):
            skipped_count += 1
            continue
        
        user = log.changed_by or 'Unknown'
        model = log.model_name
        
        if user not in logs_by_user:
            logs_by_user[user] = {}
        if model not in logs_by_user[user]:
            logs_by_user[user][model] = []
        
        logs_by_user[user][model].append(log)
        summary[log.action] += 1
    
    # Check if any real changes exist
    total_changes = sum(summary.values())
    if total_changes == 0:
        msg = "📊 <b>Daily Manager Activity Report</b>\n\n"
        msg += "No meaningful changes recorded in the last 24 hours.\n"
        if skipped_count > 0:
            msg += f"<i>({skipped_count} auto-timestamp updates filtered out)</i>"
        return [msg]
    
    # Build the report
    today = date.today().strftime("%Y-%m-%d")
    
    header = f"📊 <b>Daily Manager Activity Report</b>\n"
    header += f"📅 {today}\n\n"
    header += f"<b>Summary:</b>\n"
    header += f"➕ Creates: {summary['create']}\n"
    header += f"✏️ Updates: {summary['update']}\n"
    header += f"🗑️ Deletes: {summary['delete']}\n"
    header += f"📈 Total: {total_changes}\n"
    if skipped_count > 0:
        header += f"<i>🔇 {skipped_count} auto-updates filtered</i>\n"
    header += "\n" + "─" * 30 + "\n"
    
    # Build details section - grouped by user first, then by model
    details = ""
    
    # Sort users alphabetically
    for user in sorted(logs_by_user.keys()):
        user_models = logs_by_user[user]
        user_total = sum(len(logs) for logs in user_models.values())
        
        details += f"\n👤 <b>{user}</b> ({user_total} changes)\n"
        
        # Show models for this user (in TRACKED_MODELS order)
        for model in TRACKED_MODELS:
            if model not in user_models:
                continue
            
            model_logs = user_models[model]
            model_logs.sort(key=lambda x: x.timestamp, reverse=True)
            
            details += f"\n   📁 <b>{model}</b> ({len(model_logs)} changes)\n"
            
            for log in model_logs:
                details += format_change_detail(log) + "\n\n"
        
        details += "─" * 30 + "\n"
    
    full_message = header + details
    
    # Split if needed
    return split_message(full_message)


def send_daily_report(dry_run: bool = False):
    """Send the daily report to Telegram group (or print it if dry_run)."""
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_MANAGER_ACTIVITY_GROUP_ID")
    
    if not token and not dry_run:
        log_error(Exception("TELEGRAM_TOKEN not set"), context="telegram_manager_activity")
        return False
    
    if not chat_id and not dry_run:
        log_error(Exception("TELEGRAM_MANAGER_ACTIVITY_GROUP_ID not set"), context="telegram_manager_activity")
        return False
    
    messages = generate_daily_report()
    
    log_info(
        f"Sending daily manager activity report ({len(messages)} message(s))",
        category='notification',
        details={'message_count': len(messages)}
    )
    
    success = True
    for i, message in enumerate(messages):
        if i > 0:
            # Add continuation indicator for split messages
            message = f"<i>(continued {i+1}/{len(messages)})</i>\n\n" + message

        if dry_run:
            try:
                print("\n" + "=" * 80)
                print(message)
            except BrokenPipeError:
                # E.g. piping to `head` - stop cleanly
                return True
            continue

        if not send_telegram_message(chat_id, token, message):
            success = False
    
    return success


class Command(BaseCommandWithErrorHandling):
    help = 'Send daily manager activity report to Telegram'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Do not send to Telegram; print messages to stdout instead.'
        )

    def execute_command(self, *args, **options):
        dry_run = bool(options.get('dry_run'))
        self.stdout.write('Generating daily manager activity report...')
        
        success = send_daily_report(dry_run=dry_run)
        
        if success:
            self.stdout.write(self.style.SUCCESS('Daily manager activity report sent successfully'))
        else:
            self.stdout.write(self.style.WARNING('Failed to send some or all messages'))

