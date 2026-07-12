local typedefs = require "kong.db.schema.typedefs"

return {
  name = "gcp-service-account",
  fields = {
    {
      config = {
        type = "record",
        fields = {
          { audience = { type = "string", required = true } },
        },
      },
    },
  },
}
