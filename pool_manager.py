import datetime
import os.path as osp
import sqlite3
from threading import Lock

_db_lock = Lock()

SAVE_PATH = "eval"


def get_db_lock():
    return _db_lock


def all_evaluations_assigned():
    """
    Check if all evaluations have been assigned to evaluators
    Returns:
        bool: True if all evaluations have been assigned, False otherwise
    """
    conn = sqlite3.connect(osp.join(SAVE_PATH, "evaluations.db"))
    c = conn.cursor()
    try:
        # No existing in_progress evaluation for this user, so find a new one
        # Calculate timestamp for 30 minutes ago
        cutoff_time = datetime.datetime.now() - datetime.timedelta(minutes=30)
        cutoff_time_str = cutoff_time.strftime("%Y-%m-%d %H:%M:%S")

        # Find an available evaluation
        c.execute(
            """
            SELECT COUNT(*) FROM evaluation_pool
            WHERE 
                status = 'available' OR 
                (status = 'in_progress' AND assigned_at < ?)
            """,
            (cutoff_time_str,),
        )
        count = c.fetchone()[0]
        return count == 0
    finally:
        conn.close()


def get_sample_from_pool(user_id):
    """
    For a given user:
    1. First check if they already have an in_progress evaluation assigned to them
    2. If not, fetch a sample from the evaluation pool that:
       a. Has status='available' OR
       b. Has status='in_progress' but was assigned over 30 minutes ago
    3. Mark it as in_progress for the current user

    Args:
        user_id: The ID of the user requesting the evaluation

    Returns:
        tuple: (prompt_id, criteria_id, combo_id, turn_id) or None if no samples are available
    """
    # Acquire lock to prevent race conditions
    with _db_lock:
        conn = sqlite3.connect(
            osp.join(SAVE_PATH, "evaluations.db"), check_same_thread=True
        )
        conn.row_factory = sqlite3.Row

        c = conn.cursor()

        try:
            # First check if user already has an in_progress evaluation
            c.execute(
                """
                SELECT * FROM evaluation_pool
                WHERE 
                    user_id = ? AND 
                    status = 'in_progress'
                LIMIT 1
                """,
                (user_id,),
            )

            row = c.fetchone()

            if row:
                # User already has an in_progress evaluation
                evaluation = dict(row)

                return (
                    evaluation["prompt_id"],
                    evaluation["criteria_id"],
                    evaluation["combo_id"],
                    evaluation["turn_id"],
                )

            # No existing in_progress evaluation for this user, so find a new one
            # Calculate timestamp for 30 minutes ago
            cutoff_time = datetime.datetime.now() - datetime.timedelta(minutes=30)
            cutoff_time_str = cutoff_time.strftime("%Y-%m-%d %H:%M:%S")

            # Find an available evaluation (not assigned to this user before)
            c.execute(
                """
                SELECT * FROM evaluation_pool ep
                WHERE 
                    (ep.status = 'available' OR 
                    (ep.status = 'in_progress' AND ep.assigned_at < ?))
                    AND NOT EXISTS (
                        SELECT 1 FROM evaluations e
                        JOIN evaluation_pool seen_ep ON e.evaluation_pool_id = seen_ep.id
                        WHERE 
                            e.user_id = ? 
                            AND seen_ep.prompt_id = ep.prompt_id
                            AND seen_ep.criteria_id = ep.criteria_id
                            AND seen_ep.combo_id = ep.combo_id
                    )
                ORDER BY RANDOM()
                LIMIT 1
                """,
                (cutoff_time_str, user_id),
            )

            row = c.fetchone()

            assign_a_completed_eval = bool(
                row is None
            )  # if no available evaluations, assign a completed one
            if assign_a_completed_eval:
                # if the user completed all of the allocated evaluations, still select some at random
                c.execute(
                    """
                    SELECT * FROM evaluation_pool ep
                    WHERE 
                        (ep.user_id is NULL or ep.user_id != ?)
                        AND NOT EXISTS (
                                SELECT 1 FROM evaluations e
                                JOIN evaluation_pool seen_ep ON e.evaluation_pool_id = seen_ep.id
                                WHERE 
                                    e.user_id = ? 
                                    AND seen_ep.prompt_id = ep.prompt_id
                                    AND seen_ep.criteria_id = ep.criteria_id
                                    AND seen_ep.combo_id = ep.combo_id
                            )
                    ORDER BY RANDOM()
                    LIMIT 1
                    """,
                    (user_id, user_id),
                )

                row = c.fetchone()

            # Convert to dict
            evaluation = dict(row)

            # Mark as in_progress
            if not assign_a_completed_eval:
                c.execute(
                    """
                    UPDATE evaluation_pool 
                    SET 
                        user_id = ?,
                        status = 'in_progress', 
                        assigned_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (user_id, evaluation["id"]),
                )

                conn.commit()
            return (
                evaluation["prompt_id"],
                evaluation["criteria_id"],
                evaluation["combo_id"],
                evaluation["turn_id"],
            )

        finally:
            conn.close()
