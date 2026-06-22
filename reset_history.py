
from data.etl.schema import get_conn
c = get_conn()
c.execute("DELETE FROM player_categories WHERE player_id IN "
          "(SELECT id FROM players WHERE data_source='sr_history')")
n = c.execute("DELETE FROM players WHERE data_source='sr_history'").rowcount
c.commit()
print(n, "sr_history players cleared")