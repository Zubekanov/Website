from util.config_reader import ConfigReader

db_config = ConfigReader.get_key_value_config('database.config')
hdd_path = db_config.get('DRIVE_PATH')

