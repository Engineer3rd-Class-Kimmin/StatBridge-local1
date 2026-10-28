"""Use complete approved metadata values instead of a truncated language index."""
def refresh_classifications(tables, store):
    for table in tables:
        if table['table_id'] not in store.table_ids():
            continue
        metadata=store.get(table['table_id'])
        if getattr(metadata, 'frequencies', None):
            table['available_frequencies'] = list(metadata.frequencies)
        dimensions={d['mcp_param']:d for d in metadata.dimensions}
        for dimension in table.get('dimensions',[]):
            current=dimensions.get(dimension['api_param'])
            if current is None:
                continue
            previous={v['value_id']:v for v in dimension.get('values',[])}
            values={}
            for value in current['values']:
                identifier=value['class_id']
                values.setdefault(identifier,{**previous.get(identifier,{}),
                    'value_id':identifier,'value_name':value['class_name']})
            if values:
                # Preserve an established language-index disambiguation when
                # a metadata refresh adds another ID with the identical name.
                # Both IDs remain available for explicit classification codes.
                counts={}
                for v in values.values():
                    counts[v['value_name']]=counts.get(v['value_name'],0)+1
                for identifier,v in values.items():
                    if counts[v['value_name']]>1 and identifier in previous:
                        v['_language_index_preferred']=True
                dimension['values']=list(values.values())
                if dimension.get('representative_value_id') not in values:
                    dimension.pop('representative_value_id',None)
