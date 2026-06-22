from data.etl.schema import get_conn
for r in get_conn().execute(
    "SELECT name, college, teams, active_decades FROM players "
    "WHERE data_source='sr_history' ORDER BY name"
):
    print(f"{r[0][:22]:22} | {(r[1] or '-')[:14]:14} | {r[2]} | {r[3]}")
