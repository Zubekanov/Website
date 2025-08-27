import string
import random
from util.psql_manager import PSQLClient

client = PSQLClient()

default_display_prefix = "Tournament User "

class Tournament_User:
    def __init__(self, uid=None, user_id=None, display_name=None, created_at=None):
        self.uid = uid
        self.user_id = user_id
        self.display_name = display_name
        self.created_at = created_at

    @classmethod
    def create(cls):
        t_user =  client.insert_row("tournament_users", {
            "user_id": None,
            "display_name": Tournament_User.generate_temporary_display_name(default_display_prefix)
        }, returning=["*"])[0]
        return cls(
            uid=t_user['uid'],
            user_id=t_user['user_id'],
            display_name=t_user['display_name'],
            created_at=t_user['created_at']
        )

    @classmethod
    def load_from_id(cls, tournament_user_id):
        t_user = client.get_rows_by_conditions("tournament_users", {"id": tournament_user_id})
        if t_user:
            return cls(
                uid=t_user[0]['uid'],
                user_id=t_user[0]['user_id'],
                display_name=t_user[0]['display_name'],
                created_at=t_user[0]['created_at']
            )
        return None

    @classmethod
    def load_from_uid(cls, user_id):
        user = client.get_rows_by_conditions("users", {"uid": user_id})
        if user:
            uid = user[0]['uid']
            t_user = client.get_rows_by_conditions("tournament_users", {"user_id": uid})
            if not t_user:
                tid = Tournament_User.add_new_user(user_id=uid)
                t_user = client.get_rows_by_conditions("tournament_users", {"uid": tid})
            
            return cls( 
                uid=t_user[0]['uid'],
                user_id=t_user[0]['user_id'],
                display_name=t_user[0]['display_name'],
                created_at=t_user[0]['created_at']
            )
        return None

    @staticmethod
    def add_new_user(user_id=None, display_name=None):
        if display_name == None:
            display_name = Tournament_User.generate_temporary_display_name(default_display_prefix)
        return client.insert_row("tournament_users", {
            "user_id": user_id,
            "display_name": display_name,
        }, returning=["uid"])[0]['uid']

    @staticmethod
    def generate_temporary_display_name(prefix: str, suffix_length = 5, max_attempts = 20):
        for _ in range(max_attempts):
            suffix = str(random.randint(10**(suffix_length-1), 10**suffix_length - 1))
            name = prefix + suffix
            rows = client.get_rows_by_conditions("tournament_users", {"display_name": name})
            if not rows:
                return name
        return RuntimeError(f"Failed to generate a unique temporary display_name after {max_attempts} attempts")

    def get_uid(self):
        return self.uid

def build_html():
    pass
