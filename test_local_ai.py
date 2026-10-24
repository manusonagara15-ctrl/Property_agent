
from services.local_ai import (
    extract_property_filters,
    build_search_query,
)

message = "I need a 2 BHK apartment for rent in Ahmedabad under 20000"

try:
    filters = extract_property_filters(message)
    search_query = build_search_query(filters, message)

    print("AI extracted filters:")
    print(filters)

    print("\nQuery for the existing search service:")
    print(search_query)

except Exception as error:
    print("Test failed:", error)
