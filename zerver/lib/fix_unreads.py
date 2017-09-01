from __future__ import absolute_import
from __future__ import print_function

import time

from typing import Callable, List, TypeVar
from psycopg2.extensions import cursor
CursorObj = TypeVar('CursorObj', bound=cursor)

from django.db import connection

from zerver.lib.topic_mutes import build_topic_mute_checker
from zerver.models import UserProfile

def update_unread_flags(cursor, user_message_ids):
    # type: (CursorObj, List[int]) -> None
    um_id_list = ', '.join(str(id) for id in user_message_ids)
    query = '''
        UPDATE zerver_usermessage
        SET flags = flags | 1
        WHERE id IN (%s)
    ''' % (um_id_list,)

    cursor.execute(query)


def get_timing(message, f):
    # type: (str, Callable) -> None
    start = time.time()
    print(message)
    f()
    elapsed = time.time() - start
    print('elapsed time: %.03f\n' % (elapsed,))


def fix_unsubscribed(cursor, user_profile):
    # type: (CursorObj, UserProfile) -> None

    recipient_ids = []

    def find_recipients():
        # type: () -> None
        query = '''
            SELECT
                zerver_subscription.recipient_id
            FROM
                zerver_subscription
            INNER JOIN zerver_recipient ON (
                zerver_recipient.id = zerver_subscription.recipient_id
            )
            WHERE (
                zerver_subscription.user_profile_id = '%s' AND
                zerver_recipient.type = 2 AND
                (NOT zerver_subscription.active)
            )
        '''
        cursor.execute(query, [user_profile.id])
        rows = cursor.fetchall()
        for row in rows:
            recipient_ids.append(row[0])
        print(recipient_ids)

    get_timing(
        'get recipients',
        find_recipients
    )

    if not recipient_ids:
        return

    user_message_ids = []

    def find():
        # type: () -> None
        recips = ', '.join(str(id) for id in recipient_ids)

        query = '''
            SELECT
                zerver_usermessage.id
            FROM
                zerver_usermessage
            INNER JOIN zerver_message ON (
                zerver_message.id = zerver_usermessage.message_id
            )
            WHERE (
                zerver_usermessage.user_profile_id = %s AND
                (zerver_usermessage.flags & 1) = 0 AND
                zerver_message.recipient_id in (%s)
            )
        ''' % (user_profile.id, recips)

        print('''
            EXPLAIN analyze''' + query.rstrip() + ';')

        cursor.execute(query)
        rows = cursor.fetchall()
        for row in rows:
            user_message_ids.append(row[0])
        print('rows found: %d' % (len(user_message_ids),))

    get_timing(
        'finding unread messages for non-active streams',
        find
    )

    if not user_message_ids:
        return

    def fix():
        # type: () -> None
        update_unread_flags(cursor, user_message_ids)

    get_timing(
        'fixing unread messages for non-active streams',
        fix
    )

def fix_pre_pointer(cursor, user_profile):
    # type: (CursorObj, UserProfile) -> None

    pointer = user_profile.pointer

    if not pointer:
        return

    recipient_ids = []

    def find_non_muted_recipients():
        # type: () -> None
        query = '''
            SELECT
                zerver_subscription.recipient_id
            FROM
                zerver_subscription
            INNER JOIN zerver_recipient ON (
                zerver_recipient.id = zerver_subscription.recipient_id
            )
            WHERE (
                zerver_subscription.user_profile_id = '%s' AND
                zerver_recipient.type = 2 AND
                zerver_subscription.in_home_view AND
                zerver_subscription.active
            )
        '''
        cursor.execute(query, [user_profile.id])
        rows = cursor.fetchall()
        for row in rows:
            recipient_ids.append(row[0])
        print(recipient_ids)

    get_timing(
        'find_non_muted_recipients',
        find_non_muted_recipients
    )

    if not recipient_ids:
        return

    user_message_ids = []

    def find_old_ids():
        # type: () -> None
        recips = ', '.join(str(id) for id in recipient_ids)

        is_topic_muted = build_topic_mute_checker(user_profile)

        query = '''
            SELECT
                zerver_usermessage.id,
                zerver_message.recipient_id,
                zerver_message.subject
            FROM
                zerver_usermessage
            INNER JOIN zerver_message ON (
                zerver_message.id = zerver_usermessage.message_id
            )
            WHERE (
                zerver_usermessage.user_profile_id = %s AND
                zerver_usermessage.message_id <= %s AND
                (zerver_usermessage.flags & 1) = 0 AND
                zerver_message.recipient_id in (%s)
            )
        ''' % (user_profile.id, pointer, recips)

        print('''
            EXPLAIN analyze''' + query.rstrip() + ';')

        cursor.execute(query)
        rows = cursor.fetchall()
        for (um_id, recipient_id, topic) in rows:
            if not is_topic_muted(recipient_id, topic):
                user_message_ids.append(um_id)
        print('rows found: %d' % (len(user_message_ids),))

    get_timing(
        'finding pre-pointer messages that are not muted',
        find_old_ids
    )

    if not user_message_ids:
        return

    def fix():
        # type: () -> None
        update_unread_flags(cursor, user_message_ids)

    get_timing(
        'fixing unread messages for pre-pointer non-muted messages',
        fix
    )

def fix(user_profile):
    # type: (UserProfile) -> None
    print('\n---\nFixing %s:' % (user_profile.email,))
    with connection.cursor() as cursor:
        fix_unsubscribed(cursor, user_profile)
        fix_pre_pointer(cursor, user_profile)
        connection.commit()