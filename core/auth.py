"""
Authentication module - whitelist-based user authorization
"""
import logging
from typing import List, Set

logger = logging.getLogger(__name__)


class Auth:
    """Simple whitelist-based authentication"""
    
    def __init__(self, allowed_users: List[int]):
        """
        Initialize auth with allowed user IDs
        
        Args:
            allowed_users: List of Telegram user IDs that are authorized
        """
        self.allowed_users: Set[int] = set(allowed_users)
        logger.info(f"Auth initialized with {len(self.allowed_users)} allowed users")
    
    def check(self, user_id: int) -> bool:
        """
        Check if user is authorized
        
        Args:
            user_id: Telegram user ID to check
            
        Returns:
            True if authorized, False otherwise
        """
        is_allowed = user_id in self.allowed_users
        
        if not is_allowed:
            logger.warning(f"Unauthorized access attempt from user_id: {user_id}")
        
        return is_allowed
    
    def add_user(self, user_id: int) -> None:
        """Add a user to the whitelist"""
        self.allowed_users.add(user_id)
        logger.info(f"Added user {user_id} to whitelist")
    
    def remove_user(self, user_id: int) -> None:
        """Remove a user from the whitelist"""
        self.allowed_users.discard(user_id)
        logger.info(f"Removed user {user_id} from whitelist")
